"""보호중인 개의 대표 사진에 특징(털·귀·눈매)·품종 태그를 붙여 S3 Gold 에 저장한다 (DAG photo_tags 의 실제 처리).

두 단계로 나눠 실행한다:
  select : Silver 공고·이미지 테이블에서 대상(보호중 개의 대표 사진)을 고르고, 이미 끝난 것을 빼서 목록을 쓴다.
  run    : 목록을 읽어 사진마다 질문지 2개를 2회씩 묻고(사진당 4번), 태그·처리 기록·요청 기록을 Gold 에 쓴다.

대상과 대표 사진 (2026-11-19 결정, docs/data_contract.md 13절):
- 개(species_code 417000)이고 status_group 이 PROTECTING 인 공고.
- 대표 사진 = Silver 이미지 테이블(칸마다 최신 정상 버전만 둠)에서 original_s3_key·sha256 이 있는 가장 작은 칸.
  화면 썸네일(검색 API photos[0])과 같은 기준이다. S3 키는 만들지 않고 테이블에 적힌 키를 그대로 읽는다.
- 사진이 한 장도 없으면 호출하지 않고 처리 기록에 NO_PHOTO 로 남긴다.
- 같은 (공고, 질문지, prompt_version, sha256) 가 이미 OK·NO_RESULT 거나 FAILED 3번이면 건너뛴다.
  그래서 대표 사진이 바뀌거나(sha256) 질문지 버전을 올릴 때만 다시 부른다.

호출: OpenRouter google/gemini-3.1-flash-lite, 공급자 google-ai-studio 고정, reasoning low,
media_resolution MEDIA_RESOLUTION_MEDIUM(평가와 같은 해상도), temperature 1.0. 키는 OPENROUTER_KEY_TAG_IMG,
OpenRouter 앱 이름은 rebom-tag-photo.
- temperature: 평가는 값을 보내지 않아 Google 기본값(Gemini 3 는 1.0)으로 돌았다. 기본값이 바뀌어도 평가와 같게
  1.0 을 명시한다. seed 는 보내지 않는다. 2회 답이 같을 때만 태그를 붙이므로 두 회차는 서로 독립된 표본이어야 한다.
- 키·계정 오류(AccountError: 키 무효·크레딧 부족·키 한도)면 그 배치까지 저장하고 실패로 끝낸다.
- 특징 질문의 prompt_tokens 가 --max-trait-prompt-tokens(기본 1,500)를 넘으면 medium 해상도가 빠진 것으로 보고
  멈춘다(medium 이면 약 1,294, 기본 high 면 약 1,818). media_resolution 은 OpenRouter 문서에 없는 통과 파라미터라서다.

실행 예 (PC, 레포 루트):
  python -m rebom.tagging.run_photo_tags select --profile rebom --limit 200 --targets data/tag_img/targets.json
  python -m rebom.tagging.run_photo_tags run --profile rebom --targets data/tag_img/targets.json --local-out data/tag_img/out
  (--local-out 을 빼면 Gold 테이블에 쓴다. Gold 테이블은 rebom.common.gold_catalog --create 로 먼저 만든다)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rebom.common.openrouter import (
    AccountError,
    ChatRequest,
    ChatResult,
    OpenRouterClient,
    OpenRouterError,
)
from rebom.tagging import breed_tags, tag_codes, trait_tags
from rebom.tagging import version_config
from rebom.tagging.image_input import prepare_image

log = logging.getLogger("rebom.tagging.photo_tags")

KEY_ENV = "OPENROUTER_KEY_TAG_IMG"
APP_NAME = "rebom-tag-photo"  # OpenRouter 사용 기록에서 사진 태깅 호출만 골라 보는 앱 이름
APP_URL = "https://github.com/lemin37-dev/re-bom/tree/develop/src/rebom/tagging"
MODEL = version_config.MODEL
PROVIDER = version_config.PROVIDER
REASONING = version_config.REASONING
TEMPERATURE = version_config.TEMPERATURE
MEDIA_RESOLUTION = version_config.MEDIA_RESOLUTION
MAX_TRAIT_PROMPT_TOKENS = 1500
REPEATS = version_config.REPEATS
MAX_ATTEMPTS = 3
DOG_SPECIES_CODE = "417000"
PROTECTING = "PROTECTING"
NO_PHOTO_HASH = "no-photo"
SOURCE = "photo"
# 처리 대상의 종류(Gold 공용 키, 2026-11-20 결정). 사진 태그는 사진 sha256 단위, 사진 없음 기록은 공고 단위.
SUBJECT_PHOTO, SUBJECT_ANIMAL = "photo", "animal"
TASKS = (trait_tags.TASK, breed_tags.TASK)
VERSIONS = {trait_tags.TASK: trait_tags.PROMPT_VERSION, breed_tags.TASK: breed_tags.PROMPT_VERSION}

# 처리 기록 status
OK, NO_RESULT, FAILED, NO_PHOTO = "OK", "NO_RESULT", "FAILED", "NO_PHOTO"
DONE_STATUSES = {OK, NO_RESULT, NO_PHOTO}


class ResolutionGuardError(RuntimeError):
    """특징 질문 입력 토큰이 상한을 넘음(medium 해상도가 적용되지 않은 것으로 봄)."""


# ---------------------------------------------------------------- 대상 고르기 (순수 함수)


@dataclass(frozen=True)
class Target:
    """태깅할 사진 한 장. 같은 사진(sha256)이 여러 공고에 올라와도 한 번만 부른다."""

    sha256: str
    s3_key: str
    tasks: tuple[str, ...]  # 이번에 부를 질문지
    desertion_nos: tuple[str, ...] = ()  # 이 사진을 대표 사진으로 쓰는 공고 (기록·확인용)

    @classmethod
    def from_dict(cls, d: dict) -> Target:
        return cls(d["sha256"], d["s3_key"], tuple(d["tasks"]), tuple(str(n) for n in d.get("desertion_nos") or ()))


def representative_photos(image_rows: Iterable[dict]) -> dict[str, dict]:
    """공고별 대표 사진: original_s3_key 와 sha256 이 모두 있는 가장 작은 칸."""
    best: dict[str, dict] = {}
    for r in image_rows:
        if not r.get("original_s3_key") or not r.get("sha256") or r.get("image_slot") is None:
            continue
        no = str(r["desertion_no"])
        if no not in best or int(r["image_slot"]) < int(best[no]["image_slot"]):
            best[no] = r
    return best


def is_done(log_row: dict | None) -> bool:
    if not log_row:
        return False
    if log_row.get("status") in DONE_STATUSES:
        return True
    return log_row.get("status") == FAILED and int(log_row.get("attempts") or 0) >= MAX_ATTEMPTS


def log_key(subject_type: str, subject_key: str, task: str, version: str, input_hash: str) -> tuple:
    return (subject_type, str(subject_key), task, version, input_hash)


def log_index(log_rows: Iterable[dict]) -> dict[tuple, dict]:
    return {log_key(r["subject_type"], r["subject_key"], r["task"], r["version"], r["input_hash"]): r for r in log_rows}


def _photo_key(sha: str, task: str, version: str) -> tuple:
    return log_key(SUBJECT_PHOTO, sha, task, version, sha)


def select_targets(
    dog_nos: Iterable[str],
    image_rows: Iterable[dict],
    log_rows: Iterable[dict],
    *,
    versions: dict[str, str] = VERSIONS,
    limit: int | None = None,
) -> tuple[list[Target], list[str]]:
    """(부를 사진, 새로 NO_PHOTO 로 적을 공고).

    보호중 개마다 대표 사진을 고른 뒤 sha256 으로 묶는다. 같은 사진은 한 번만 부르고, 그 사진·질문지 버전으로 이미 끝났으면
    부르지 않는다. 사진 순서는 그 사진을 쓰는 가장 작은 공고번호 순이라 재실행해도 같다.
    """
    reps = representative_photos(image_rows)
    done = log_index(log_rows)
    photos: dict[str, dict] = {}  # sha256 -> {"s3_key", "nos"}
    no_photo: list[str] = []
    for no in sorted({str(n) for n in dog_nos}):
        rep = reps.get(no)
        if rep is None:
            if not all(is_done(done.get(log_key(SUBJECT_ANIMAL, no, t, v, NO_PHOTO_HASH))) for t, v in versions.items()):
                no_photo.append(no)
            continue
        sha = str(rep["sha256"]).lower()
        photos.setdefault(sha, {"s3_key": rep["original_s3_key"], "nos": []})["nos"].append(no)
    targets: list[Target] = []
    for sha, p in photos.items():
        tasks = tuple(t for t, v in versions.items() if not is_done(done.get(_photo_key(sha, t, v))))
        if tasks:
            targets.append(Target(sha, p["s3_key"], tasks, tuple(p["nos"])))
    if limit:
        targets = targets[:limit]
    return targets, no_photo


def pending_targets(targets: Iterable[Target], log_rows: Iterable[dict], versions: dict[str, str] = VERSIONS) -> list[Target]:
    """대상 목록을 만든 뒤 다른 실행이 끝낸 질문지를 뺀다."""
    done = log_index(log_rows)
    out = []
    for t in targets:
        tasks = tuple(task for task in t.tasks if not is_done(done.get(_photo_key(t.sha256, task, versions[task]))))
        if tasks:
            out.append(Target(t.sha256, t.s3_key, tasks, t.desertion_nos))
    return out


# ---------------------------------------------------------------- 호출과 판정


@dataclass
class TaskOutcome:
    task: str
    status: str
    tag_rows: list[dict] = field(default_factory=list)
    error: str | None = None


@dataclass
class PhotoOutcome:
    target: Target
    tasks: dict[str, TaskOutcome]
    requests: list[dict]


def _now() -> datetime:
    return datetime.now(UTC)


def _request_for(task: str, image: bytes, mime: str) -> ChatRequest:
    if task == trait_tags.TASK:
        system, prompt, schema, name = trait_tags.SYSTEM_PROMPT, trait_tags.USER_PROMPT, trait_tags.response_schema(), "trait"
    else:
        system, prompt, schema, name = breed_tags.SYSTEM_PROMPT, breed_tags.build_prompt(), breed_tags.response_schema(), "breed"
    return ChatRequest(
        model=MODEL, provider=PROVIDER, system=system, prompt=prompt, schema=schema, schema_name=name,
        image=image, image_mime=mime, reasoning_effort=REASONING, media_resolution=MEDIA_RESOLUTION,
        temperature=TEMPERATURE,
    )


def _request_row(target: Target, task: str, run_no: int, result: ChatResult | None, *, status: str,
                 error: Exception | None, requested_at: datetime, canonical_slug: str | None, run_id: str) -> dict:
    return {
        "request_id": result.request_id if result else None,
        "subject_type": SUBJECT_PHOTO,
        "subject_key": target.sha256,
        "task": task,
        "prompt_version": VERSIONS[task],
        "run_no": run_no,
        "requested_model": MODEL,
        "response_model": result.response_model if result else None,
        "provider": result.provider if result else None,
        "canonical_slug": canonical_slug,
        "media_resolution": MEDIA_RESOLUTION,
        "temperature": TEMPERATURE,
        "reasoning_effort": REASONING,
        "finish_reason": result.finish_reason if result else None,
        "native_finish_reason": result.native_finish_reason if result else None,
        "prompt_tokens": result.prompt_tokens if result else None,
        "completion_tokens": result.completion_tokens if result else None,
        "reasoning_tokens": result.reasoning_tokens if result else None,
        "cost_usd": float(result.cost_usd) if result and result.cost_usd is not None else None,
        "latency_ms": result.latency_ms if result else None,
        "attempts": result.attempts if result else None,
        "status": status,
        "error_code": (getattr(error, "code", None) or type(error).__name__) if error else None,
        "raw_answer": result.content[:8000] if result else (str(error)[:500] if error else None),
        "requested_at": requested_at,
        "gold_run_id": run_id,
        "dt": requested_at.astimezone(UTC).strftime("%Y-%m-%d"),
    }


def trait_tag_rows(target: Target, answers: Sequence[trait_tags.TraitAnswer], run_id: str, now: datetime) -> list[dict]:
    detail = json.dumps(
        [{"fur": a.fur, "ears": a.features.get("ears"), "eyes": a.features.get("eyes")} for a in answers],
        ensure_ascii=False,
    )
    rows = []
    for code in trait_tags.decide_tags(answers):
        t = tag_codes.get(code)
        rows.append(_tag_row(target, trait_tags.TASK, code, t, None, None, detail, run_id, now))
    return rows


def breed_tag_rows(target: Target, answers: Sequence[breed_tags.BreedAnswer], run_id: str, now: datetime) -> list[dict]:
    """두 회차 모두 Top3 에 든 품종마다 한 행. 강 신호만 searchable. detail 에 두 회차 Top3 원값 전체."""
    result = breed_tags.decide(answers)
    detail = json.dumps([a.to_dict() for a in answers], ensure_ascii=False)
    strong = set(result.strong)
    rows = []
    for name, rank, avg in result.ranked:
        t = tag_codes.breed_tag(name)
        row = _tag_row(target, breed_tags.TASK, t.code, t, rank, round(avg), detail, run_id, now)
        row["searchable"] = name in strong
        rows.append(row)
    return rows


def _tag_row(target, task, code, tdef, rank, similarity, detail, run_id, now) -> dict:
    return {
        "subject_type": SUBJECT_PHOTO,
        "subject_key": target.sha256,
        "tag_code": code,
        "source": SOURCE,
        "tagger_version": f"{MODEL}@{VERSIONS[task]}",
        "task": task,
        "model": MODEL,
        "prompt_version": VERSIONS[task],
        "source_hash": target.sha256,
        "searchable": tdef.searchable,
        "displayable": tdef.displayable,
        "rank": rank,
        "similarity": similarity,
        "detail": detail,
        "gold_run_id": run_id,
        "gold_updated_at": now,
    }


@dataclass
class TaggerConfig:
    run_id: str
    concurrency: int = 4
    max_trait_prompt_tokens: int = MAX_TRAIT_PROMPT_TOKENS
    canonical_slug: str | None = None


def tag_photos(
    items: Sequence[tuple[Target, bytes | Exception]],
    client: OpenRouterClient,
    cfg: TaggerConfig,
    *,
    now: Callable[[], datetime] = _now,
) -> tuple[list[PhotoOutcome], BaseException | None]:
    """사진들을 태깅한다. 돌려주는 두 번째 값은 실행을 멈춰야 하는 오류(키·계정 오류·해상도 감시) 또는 None.

    멈춰야 하는 오류가 나도 이미 끝난 사진의 결과는 돌려준다. 그 오류에 걸린 사진은 결과에서 뺀다(다음 실행에서 다시 한다).
    """
    calls = []  # (사진 순번, task, run_no, ChatRequest | 오류)
    for i, (target, data) in enumerate(items):
        prepared: tuple[bytes, str] | Exception
        if isinstance(data, Exception):
            prepared = data
        else:
            try:
                prepared = prepare_image(data)
            except Exception as exc:  # noqa: BLE001 - 깨진 사진은 그 사진만 FAILED
                prepared = exc
        for task in target.tasks:
            for run_no in range(1, REPEATS + 1):
                req = prepared if isinstance(prepared, Exception) else _request_for(task, *prepared)
                calls.append((i, task, run_no, req))

    stop: list[BaseException] = []

    def one(call):
        _, task, _, req = call
        at = now()
        if stop:
            return call, None, None, at, True
        if isinstance(req, Exception):
            return call, None, req, at, False
        try:
            res = client.chat(req)
        except AccountError as exc:
            stop.append(exc)
            return call, None, exc, at, True
        except OpenRouterError as exc:
            return call, None, exc, at, False
        if task == trait_tags.TASK and res.prompt_tokens > cfg.max_trait_prompt_tokens:
            stop.append(ResolutionGuardError(
                f"특징 질문 입력 {res.prompt_tokens}토큰 > {cfg.max_trait_prompt_tokens}: medium 해상도가 빠진 것으로 보임"
            ))
            return call, res, None, at, True
        return call, res, None, at, False

    with ThreadPoolExecutor(max(1, cfg.concurrency)) as pool:
        results = list(pool.map(one, calls))

    outcomes: list[PhotoOutcome] = []
    by_photo: dict[int, list] = {}
    for item in results:
        by_photo.setdefault(item[0][0], []).append(item)
    for i, (target, _) in enumerate(items):
        got = by_photo.get(i, [])
        if any(aborted for *_, aborted in got):
            # 멈춘 사진은 요청 기록만 남기고(실제로 나간 요청), 처리 기록·태그는 쓰지 않는다
            reqs = [
                _request_row(target, c[1], c[2], res, status="OK" if res else "ERROR", error=err, requested_at=at,
                             canonical_slug=cfg.canonical_slug, run_id=cfg.run_id)
                for c, res, err, at, _ in got
                if res is not None or isinstance(err, OpenRouterError)
            ]
            if reqs:
                outcomes.append(PhotoOutcome(target, {}, reqs))
            continue
        outcomes.append(_photo_outcome(target, got, cfg, now()))
    return outcomes, (stop[0] if stop else None)


def _photo_outcome(target: Target, got: list, cfg: TaggerConfig, ts: datetime) -> PhotoOutcome:
    requests_rows: list[dict] = []
    tasks: dict[str, TaskOutcome] = {}
    per_task: dict[str, list] = {}
    for call, res, err, at, _ in got:
        _, task, run_no, _ = call
        parsed = None
        status = "OK"
        if err is None and res is not None:
            try:
                parsed = (trait_tags.parse_answer if task == trait_tags.TASK else breed_tags.parse_answer)(res.json())
            except (ValueError, KeyError, TypeError) as exc:
                err, status = exc, "BAD_ANSWER"
        if err is not None and status == "OK":
            status = "ERROR"
        if res is not None or isinstance(err, OpenRouterError):
            requests_rows.append(_request_row(target, task, run_no, res, status=status, error=err, requested_at=at,
                                              canonical_slug=cfg.canonical_slug, run_id=cfg.run_id))
        per_task.setdefault(task, []).append((run_no, parsed, err))
    for task, runs in per_task.items():
        runs.sort(key=lambda x: x[0])
        errors = [e for _, p, e in runs if p is None]
        if errors:
            tasks[task] = TaskOutcome(task, FAILED, error=f"{type(errors[0]).__name__}: {errors[0]}"[:500])
            continue
        answers = [p for _, p, _ in runs]
        rows = (trait_tag_rows if task == trait_tags.TASK else breed_tag_rows)(target, answers, cfg.run_id, ts)
        found = any(r["searchable"] for r in rows) if task == breed_tags.TASK else bool(rows)
        tasks[task] = TaskOutcome(task, OK if found else NO_RESULT, tag_rows=rows)
    return PhotoOutcome(target, tasks, requests_rows)


def inference_rows(outcomes: Sequence[PhotoOutcome], previous: dict[tuple, dict], run_id: str, ts: datetime,
                   versions: dict[str, str] = VERSIONS) -> list[dict]:
    rows = []
    for o in outcomes:
        for task, t in o.tasks.items():
            key = _photo_key(o.target.sha256, task, versions[task])
            attempts = int((previous.get(key) or {}).get("attempts") or 0) + 1
            rows.append({
                "subject_type": SUBJECT_PHOTO,
                "subject_key": o.target.sha256,
                "task": task,
                "version": versions[task],
                "input_hash": o.target.sha256,
                "status": t.status,
                "attempts": attempts,
                "tag_count": sum(1 for r in t.tag_rows if r["searchable"]),
                "last_error": t.error,
                "gold_run_id": run_id,
                "gold_updated_at": ts,
            })
    return rows


def no_photo_rows(nos: Iterable[str], run_id: str, ts: datetime,
                  versions: dict[str, str] = VERSIONS) -> list[dict]:
    return [
        {
            "subject_type": SUBJECT_ANIMAL, "subject_key": no, "task": task, "version": version,
            "input_hash": NO_PHOTO_HASH, "status": NO_PHOTO, "attempts": 1, "tag_count": 0, "last_error": None, "gold_run_id": run_id,
            "gold_updated_at": ts,
        }
        for no in nos
        for task, version in versions.items()
    ]


def replaced_tag_rows(outcomes: Sequence[PhotoOutcome],
                      registered: dict[str, str] | None = None) -> tuple[list[tuple[str, str, str]], list[dict]]:
    """((사진 sha256, 작업, 질문지 버전) 교체 범위, 새 태그 행).

    교체는 같은 사진·같은 질문지 버전으로 다시 태깅한 경우만이다. 공고의 사진이 바뀌면 새 sha256 이 새 행이 되고,
    질문지 버전을 올려도 이전 행은 그대로 남는다(이전 버전 보존, 2026-11-20 결정). FAILED 인 작업은 교체하지 않는다.
    """
    scope, rows = [], []
    versions = registered or VERSIONS
    for o in outcomes:
        for task, t in o.tasks.items():
            if t.status in (OK, NO_RESULT):
                scope.append((o.target.sha256, task, versions[task]))
                if registered:
                    rows.extend(r | {"version": versions[task]} for r in t.tag_rows)
                else:
                    rows.extend(t.tag_rows)
    return scope, rows


# ---------------------------------------------------------------- 저장소 (Gold / 로컬)


class LocalSink:
    """Gold 대신 로컬 JSONL 에 쓴다 (PC 샘플 시험용). 처리 기록은 다음 select 에서 읽을 수 있게 합쳐 둔다."""

    def __init__(self, out_dir: Path):
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)

    def _append(self, name: str, rows: Sequence[dict]) -> None:
        with (self.out / name).open("a", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    def read_inference_log(self) -> list[dict]:
        p = self.out / "gold_inference_log.jsonl"
        latest: dict[tuple, dict] = {}
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                r = json.loads(line)
                latest[log_key(r["subject_type"], r["subject_key"], r["task"], r["version"], r["input_hash"])] = r
        return list(latest.values())

    def write(self, scope, tag_rows, log_rows, request_rows) -> None:
        self._append("gold_animal_tag.jsonl", tag_rows)
        self._append("gold_inference_log.jsonl", log_rows)
        self._append("gold_model_request_log.jsonl", request_rows)


class GoldSink:
    """Gold Iceberg 테이블에 쓴다.

    태그는 (photo, 사진 sha256, 작업, 질문지 버전) 범위만 교체하고 이전 사진·이전 질문지 버전의 행은 남긴다.
    처리 기록은 upsert, 요청 기록은 append.
    """

    def __init__(self, catalog, env, *, registered: bool = False):
        from rebom.common import gold_catalog as gc
        from rebom.common.gold_version_registry import require_version_column

        self.gc = gc
        self.tag = catalog.load_table(env.table(gc.TAG_TABLE))
        self.inference = catalog.load_table(env.table(gc.INFERENCE_LOG_TABLE))
        self.requests = catalog.load_table(env.table(gc.REQUEST_LOG_TABLE))
        self.registered = registered
        self.has_version = any(field.name == "version" for field in self.tag.schema().fields)
        if registered:
            require_version_column(self.tag)
            require_version_column(self.requests)

    def read_inference_log(self) -> list[dict]:
        """사진 태깅 행만. 처리 기록은 글 태깅·임베딩과 같이 쓰는 공용 로그다."""
        from pyiceberg.expressions import In

        return self.inference.scan(row_filter=In("task", sorted(VERSIONS))).to_arrow().to_pylist()

    @staticmethod
    def _arrow(table, rows):
        import pyarrow as pa

        return pa.Table.from_pylist(rows, schema=table.schema().as_arrow())

    def write(self, scope, tag_rows, log_rows, request_rows) -> None:
        if request_rows:
            self.requests.append(self._arrow(self.requests, request_rows))
        if scope:
            by_group: dict[tuple[str, str], list[str]] = {}
            for sha, task, version in scope:
                by_group.setdefault((task, version), []).append(sha)
            for (task, version), shas in by_group.items():
                column = "version" if self.registered else "prompt_version"
                rows = [r for r in tag_rows if r["task"] == task and r[column] == version]
                self.tag.overwrite(self._arrow(self.tag, rows), overwrite_filter=tag_scope_filter(
                    task, version, shas, registered=self.registered, has_version=self.has_version,
                ))
        if log_rows:
            self.inference.upsert(self._arrow(self.inference, log_rows), join_cols=list(self.gc.INFERENCE_LOG_KEYS))


def tag_scope_filter(task: str, version: str, shas: Sequence[str], *,
                     registered: bool = False, has_version: bool = False):
    """교체할 태그 행: source=photo, subject_type=photo, 이 작업·질문지 버전, 그리고 이 사진들."""
    from pyiceberg.expressions import And, EqualTo, In, IsNull

    version_filter = EqualTo("version" if registered else "prompt_version", version)
    if not registered and has_version:
        version_filter = And(version_filter, IsNull("version"))

    return And(
        And(And(EqualTo("source", SOURCE), EqualTo("subject_type", SUBJECT_PHOTO)), EqualTo("task", task)),
        And(version_filter, In("subject_key", sorted(set(shas)))),
    )


# ---------------------------------------------------------------- Silver 읽기·S3


def read_silver(catalog, silver_env) -> tuple[list[str], list[dict]]:
    """(보호중 개 공고번호, 이미지 현재 행)."""
    from pyiceberg.expressions import And, EqualTo

    notices = catalog.load_table(silver_env.notice_table).scan(
        row_filter=And(EqualTo("species_code", DOG_SPECIES_CODE), EqualTo("status_group", PROTECTING)),
        selected_fields=("desertion_no",),
    ).to_arrow().to_pylist()
    dogs = [r["desertion_no"] for r in notices]
    images = catalog.load_table(silver_env.image_table).scan(
        selected_fields=("desertion_no", "image_slot", "original_s3_key", "sha256"),
    ).to_arrow().to_pylist()
    wanted = set(dogs)
    return dogs, [r for r in images if r["desertion_no"] in wanted]


def fetch_image(s3, bucket: str, target: Target) -> bytes | Exception:
    """원본을 받아 sha256 을 Silver 값과 대조한다. 다르면 그 사진만 실패."""
    try:
        data = s3.get_object(Bucket=bucket, Key=target.s3_key)["Body"].read()
    except Exception as exc:  # noqa: BLE001
        return RuntimeError(f"S3 읽기 실패 {target.s3_key}: {exc}")
    digest = hashlib.sha256(data).hexdigest()
    if digest != target.sha256:
        return RuntimeError(f"SHA_MISMATCH {target.s3_key}: silver {target.sha256[:12]} != s3 {digest[:12]}")
    return data


def _read_json(path: str, s3, bucket: str):
    if path.startswith("s3://"):
        b, _, key = path[5:].partition("/")
        return json.loads(s3.get_object(Bucket=b, Key=key)["Body"].read())
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str, data, s3) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=1)
    if path.startswith("s3://"):
        b, _, key = path[5:].partition("/")
        s3.put_object(Bucket=b, Key=key, Body=text.encode("utf-8"), ContentType="application/json")
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- CLI


def _clients(args):
    import boto3

    from rebom.storage.silver.aws_catalog import get_aws_catalog

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    return get_aws_catalog(profile=args.profile), session.client("s3", region_name="ap-northeast-2")


def _default_targets(args) -> str:
    from rebom.common.gold_catalog import get_gold_environment

    if args.targets:
        return args.targets
    if not args.run_id:
        raise SystemExit("--targets 나 --run-id 중 하나가 필요합니다")
    return f"s3://{args.bucket}/{get_gold_environment(args.gold_env).work_prefix()}/{args.run_id}/targets.json"


def _sink(args, catalog, registered: dict[str, str] | None = None):
    if args.local_out:
        if registered:
            raise ValueError("Registered versions require the Gold Iceberg sink")
        return LocalSink(Path(args.local_out))
    from rebom.common.gold_catalog import get_gold_environment
    from rebom.common.gold_version_registry import verify_runtime_config

    env = get_gold_environment(args.gold_env)
    for task, version in (registered or {}).items():
        verify_runtime_config(catalog, env, task, version, version_config.runtime_config(task))
    return GoldSink(catalog, env, registered=bool(registered))


def _registered_versions(values: Sequence[str]) -> dict[str, str]:
    versions = {}
    for version in values:
        task = version.partition("@")[0]
        if task not in VERSIONS or task in versions:
            raise ValueError(f"Expected one registered version per photo tagging task: {version}")
        versions[task] = version
    return versions


def cmd_select(args) -> int:
    from rebom.storage.silver.environment_config import get_silver_environment

    catalog, s3 = _clients(args)
    registered = _registered_versions(getattr(args, "version", ()))
    versions = registered or VERSIONS
    sink = _sink(args, catalog, registered)
    dogs, images = read_silver(catalog, get_silver_environment(args.silver_env))
    targets, no_photo = select_targets(dogs, images, sink.read_inference_log(), versions=versions, limit=args.limit or None)
    calls = sum(len(t.tasks) for t in targets) * REPEATS
    summary = {
        "run_id": args.run_id, "selected_at": _now().isoformat(), "silver_env": args.silver_env,
        "protecting_dogs": len(set(dogs)), "targets": len(targets), "target_dogs": sum(len(t.desertion_nos) for t in targets),
        "no_photo_new": len(no_photo), "calls": calls,
        "prompt_versions": VERSIONS, "items": [asdict(t) for t in targets],
    }
    if registered:
        summary["versions"] = registered
    print(f"보호중 개 {summary['protecting_dogs']}마리, 대상 사진 {len(targets)}장({summary['target_dogs']}마리, 호출 {calls}번), "
          f"새 NO_PHOTO {len(no_photo)}마리")
    if args.dry_run:
        print("dry-run: 목록·처리 기록을 쓰지 않습니다")
        return 0
    _write_json(_default_targets(args), summary, s3)
    if no_photo:
        sink.write([], [], no_photo_rows(no_photo, args.run_id or "manual", _now(), versions), [])
    return 0


def cmd_run(args) -> int:
    catalog, s3 = _clients(args)
    data = _read_json(_default_targets(args), s3, args.bucket)
    registered = _registered_versions(tuple(data.get("versions", {}).values()))
    explicit = _registered_versions(getattr(args, "version", ()))
    if explicit and explicit != registered:
        raise ValueError("Run versions must match the target list. Select targets again for another version.")
    versions = registered or VERSIONS
    targets = [Target.from_dict(d) for d in data["items"]]
    sink = _sink(args, catalog, registered)
    # 목록을 만든 뒤 다른 실행이 끝낸 것은 건너뛴다
    targets = pending_targets(targets, sink.read_inference_log(), versions)
    if args.max_photos:
        targets = targets[: args.max_photos]
    print(f"태깅할 사진 {len(targets)}장")
    if args.dry_run or not targets:
        return 0
    client = OpenRouterClient(KEY_ENV, app_name=APP_NAME, app_url=APP_URL)
    run_id = args.run_id or f"manual-{_now():%Y%m%dT%H%M%SZ}"
    cfg = TaggerConfig(run_id=run_id, concurrency=args.concurrency, max_trait_prompt_tokens=args.max_trait_prompt_tokens,
                       canonical_slug=client.canonical_slug(MODEL))
    previous = log_index(sink.read_inference_log())
    totals = {"photos": 0, "cost": 0.0, OK: 0, NO_RESULT: 0, FAILED: 0}
    for start in range(0, len(targets), args.batch_size):
        batch = targets[start:start + args.batch_size]
        items = [(t, fetch_image(s3, args.bucket, t)) for t in batch]
        outcomes, stop = tag_photos(items, client, cfg)
        ts = _now()
        scope, tag_rows = replaced_tag_rows(outcomes, registered)
        logs = inference_rows(outcomes, previous, run_id, ts, versions)
        reqs = [r for o in outcomes for r in o.requests]
        if registered:
            reqs = [r | {"version": versions[r["task"]]} for r in reqs]
        sink.write(scope, tag_rows, logs, reqs)
        previous.update(log_index(logs))
        totals["photos"] += sum(1 for o in outcomes if o.tasks)
        totals["cost"] += sum(r["cost_usd"] or 0 for r in reqs)
        for r in logs:
            totals[r["status"]] = totals.get(r["status"], 0) + 1
        print(f"{start + len(batch)}/{len(targets)}장 처리, 누적 비용 ${totals['cost']:.4f}, "
              f"OK {totals[OK]} · 태그 없음 {totals[NO_RESULT]} · 실패 {totals[FAILED]}")
        if stop is not None:
            print(f"중단: {stop}", file=sys.stderr)
            return 2
    print(f"끝: 사진 {totals['photos']}장, 비용 ${totals['cost']:.4f}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["select", "run"])
    ap.add_argument("--run-id", default=None, help="DAG 실행 id. --targets 를 비우면 목록 위치를 이 값으로 정한다")
    ap.add_argument("--targets", default=None, help="대상 목록 JSON (로컬 경로 또는 s3://)")
    ap.add_argument("--bucket", default="rebom-data-559987919299")
    ap.add_argument("--silver-env", default="test")
    ap.add_argument("--gold-env", default="test")
    ap.add_argument("--version", action="append", default=[], help="등록된 작업 버전, 예: photo_trait@1. 작업별 반복 지정")
    ap.add_argument("--profile", default=None, help="AWS 프로필 (PC). 파드에서는 비운다")
    ap.add_argument("--local-out", default=None, help="Gold 대신 이 폴더에 JSONL 로 쓴다 (PC 시험용)")
    ap.add_argument("--limit", type=int, default=0, help="select: 대상 사진 상한 (샘플 시험용)")
    ap.add_argument("--max-photos", type=int, default=0, help="run: 이번 실행 상한. 일일 증분은 1000, 백필은 0(상한 없음)")
    ap.add_argument("--batch-size", type=int, default=200, help="이 장수마다 Gold 에 쓴다")
    ap.add_argument("--concurrency", type=int, default=4, help="동시 요청 수")
    ap.add_argument("--max-trait-prompt-tokens", type=int, default=MAX_TRAIT_PROMPT_TOKENS)
    ap.add_argument("--dry-run", action="store_true", help="대상만 세고 쓰거나 부르지 않는다")
    args = ap.parse_args(argv)
    return cmd_select(args) if args.command == "select" else cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
