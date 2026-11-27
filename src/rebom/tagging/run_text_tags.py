"""보호중 공고의 글(info_text)에 성격·건강정보 태그를 붙여 S3 Gold 에 저장한다 (Jev 글 태깅 v1, docs/data_contract.md 15절).

두 단계로 나눠 실행한다(사진 태깅 run_photo_tags 와 같은 구조):
  select : Silver 공고 표에서 보호중 개·고양이의 글을 읽어 정리하고, 이미 끝난 것을 빼서 대상 목록을 쓴다.
  run    : 목록을 읽어 글마다 Jev 에 한 번 묻고(성격 8 + 건강 1 질문), 태그·처리 기록·요청 기록을 Gold 에 쓴다.

입력과 대상 (2026-11-23):
- 글 = Silver info_text(sfe_soci + sfe_health + special_mark 를 이어 붙인 값). 비어 있으면 special_mark.
  sfe 칸이 있는 공고(보호중 약 2%)는 성격·건강 내용이 sfe 에만 있는 경우가 있어 info_text 를 쓴다.
- 보내기 전 정리는 A2 글 카드(rebom.embedding.text_card.persona)와 같은 규칙이다(2026-11-23 결정): 앞의 관리번호를 지우고,
  공백을 한 칸으로 줄이고, 전화번호를 [번호]로 가린다(외부 API 로 개인정보를 보내지 않음). 정규식도 text_card 것을 그대로 쓴다.
- 정리한 글이 비었거나 자리채움 값(".", "없음", "해당없음", "특이사항 없음" 등)이면 부르지 않고 NO_TEXT 로 남긴다.
  "이상 없음"은 건강정보가 있는 글이라 자리채움 값이 아니다.
- 대상 = status_group PROTECTING, species_code 개(417000)·고양이(422400). --species 로 바꿀 수 있다.
- input_hash = 정리한 글의 sha256. 같은 공고·같은 버전·같은 input_hash 가 이미 OK·NO_RESULT·NO_TEXT 거나 FAILED 3번이면
  건너뛴다. 그래서 글이 바뀌거나(input_hash) 질문 버전을 올릴 때만 다시 부른다.
- 버전 = Gold 레지스트리에 등록한 이름(text_personality@1·text_health@1, REGISTERED). 처리 기록의 version, 태그의 version 칸,
  태그 교체 범위가 이 이름을 쓴다. 질문지 버전(text-personality-v1 등)은 태그의 prompt_version·tagger_version 에 그대로 남는다.
  Gold 에 쓰기 전에 지금 코드의 설정(runtime_config)이 레지스트리 등록값과 같은지 확인한다(다르면 멈춤, 사진 태깅과 같은 규칙).
  2026-11-25 _test 의 옛 행은 scripts/serve/fill_legacy_text_tag_versions.py 로 같은 이름으로 바뀌었다.
  후보 버전은 select·run 에 --version text_personality@2 처럼 준다(작업마다 한 번, 안 준 작업은 @1). 등록돼 있어야 하고,
  select 가 대상 목록에 적은 버전과 run 의 --version 이 다르면 멈춘다(사진 태깅 --version 과 같은 방식).
- 같은 실행 안에서 글이 같은 공고는 한 번만 부르고 결과를 나눠 쓴다(보호중 18,730건 중 서로 다른 글 약 1.25만, 2026-11-23 실측).

저장 (Gold 태깅 묶음 표 3개, 새 표 없음):
- gold_animal_tag: subject_type=animal, subject_key=공고번호, source=text. 작업 text_personality(성격 태그, 0개 이상),
  text_health(health.info 또는 health.none 1행). 교체 범위는 (공고, 작업, 버전, source_hash=input_hash) 다.
  글이 바뀌거나 질문 버전을 올리면 새 행이 생기고 이전 글·이전 버전 행은 남는다(Gold 이전 버전 보존, 2026-11-20 팀 결정).
  같은 글을 다시 태깅할 때(실패 재시도, 저장 도중 끊긴 실행)만 그 글의 행을 교체해 중복을 막는다.
  detail = 확률 전체·review·request_id·input_text(Jev 에 보낸 가린 글).
- gold_inference_log: (animal, 공고번호, 작업, 버전, input_hash) upsert. status OK·NO_RESULT·FAILED·NO_TEXT.
- gold_model_request_log: Jev 요청 1번 = 1행(task text_jev). raw_answer = 응답 answers 전체(원 확률).

호출: OpenRouter Decisions API typesafe/jev-1.14, 공급자 TypeSafe 고정(jev_client). 키 OPENROUTER_KEY_TAG_JEV.
키는 환경변수 → 레포 .env → Secrets Manager(REBOM_JEV_SECRET_ID, 기본 rebom/tag/prod/jev_api_key) 순서로 찾는다(--key-source auto).
운영 적재는 --key-source secrets 로 Secrets Manager 의 운영 키만 쓴다(.env 의 시험 키가 잡히지 않게). 서버 파드는 노드 역할로 읽는다.
키 값은 출력하지 않고 어디서 읽었는지만 알린다. 키·계정 오류면 그 배치까지 저장하고 멈춘다(종료 코드 2).

실행 예 (PC, 레포 루트):
  python -m rebom.tagging.run_text_tags select --profile <프로필> --limit 50 --targets data/tag_text/targets.json
  python -m rebom.tagging.run_text_tags run --profile <프로필> --targets data/tag_text/targets.json --local-out data/tag_text/out
  python -m rebom.tagging.run_text_tags run --profile <프로필> --targets data/tag_text/targets.json     (Gold test 에 쓰기)
  python -m rebom.tagging.run_text_tags select --profile <프로필> --silver-as-of 2026-11-23 --targets ...   (11/23 끝 KST 기준 Silver 스냅샷으로 고르기)
  python -m rebom.tagging.run_text_tags select --profile <프로필> --version text_personality@2 --targets ...  (등록한 후보 버전)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rebom.common.openrouter import AccountError, OpenRouterError, load_key
from rebom.embedding.text_card import LEADING_ID, PHONE  # A2 글 카드와 같은 정리 규칙(앞 관리번호·전화번호)
from rebom.tagging import text_tags as tt
from rebom.tagging.jev_client import MODEL, DecisionResult, JevClient
from rebom.tagging.run_photo_tags import (
    LocalSink,
    _read_json,
    _snapshot_id,
    _write_json,
    log_index,
    log_key,
    silver_cutoff,
)

log = logging.getLogger("rebom.tagging.text_tags")

KEY_ENV = "OPENROUTER_KEY_TAG_JEV"
SECRET_ID_ENV = "REBOM_JEV_SECRET_ID"
DEFAULT_SECRET_ID = "rebom/tag/prod/jev_api_key"  # 운영 키 (2026-11-26). rebom/ 로 시작해야 노드 역할이 읽는다
SECRET_FIELDS = (KEY_ENV, "jev_api_key", "api_key", "key")
KEY_SOURCES = ("auto", "env", "secrets")
SOURCE = "text"  # 태그를 만든 입력 칸. 사진 태그의 source=photo 와 같은 뜻 (2026-11-24 jev → text)
SUBJECT_ANIMAL = "animal"
PROTECTING = "PROTECTING"
DEFAULT_SPECIES = ("417000", "422400")  # 개, 고양이
NO_TEXT_HASH = "no-text"
TASKS = (tt.TASK_PERSONALITY, tt.TASK_HEALTH)
VERSIONS = tt.VERSIONS  # 질문지 버전 (prompt_version·tagger_version 에 남김)
# Gold 레지스트리 등록 버전 (처리 기록·태그의 version 칸, 교체 범위). 질문지를 바꾸면 새 버전(task@N+1)을 등록하고 여기를 바꾼다
REGISTERED = {tt.TASK_PERSONALITY: f"{tt.TASK_PERSONALITY}@1", tt.TASK_HEALTH: f"{tt.TASK_HEALTH}@1"}
# 아래 함수들의 versions 인자 = 작업별 등록 버전. 비우면 REGISTERED. 후보 버전은 --version 으로 준다(사진 태깅과 같은 방식)
OK, NO_RESULT, FAILED, NO_TEXT = "OK", "NO_RESULT", "FAILED", "NO_TEXT"
DONE_STATUSES = {OK, NO_RESULT, NO_TEXT}
MAX_ATTEMPTS = 3

PLACEHOLDERS = {"없음", "해당없음", "해당 없음", "특이사항없음", "특이사항 없음", "특이 사항 없음", "x", "무", "n/a", "na"}


# ---------------------------------------------------------------- 글 정리 (순수 함수)


def clean_text(info_text: str | None, special_mark: str | None = None) -> str:
    """Jev 에 보낼 글: info_text(없으면 special_mark) → 앞 관리번호 지움 → 공백 정리 → 전화번호 가림.

    A2 글 카드의 특징 글(text_card.persona)과 같은 순서·같은 정규식이다.
    """
    raw = info_text if info_text is not None and str(info_text).strip() else special_mark
    text = LEADING_ID.sub("", str(raw or ""), count=1)
    text = re.sub(r"\s+", " ", text).strip()
    return PHONE.sub("[번호]", text)


def is_blank(text: str) -> bool:
    """내용 없는 글. 마침표·대시만 있거나 자리채움 값."""
    core = text.strip().strip(".-·,~ ").strip()
    return not core or core.lower() in PLACEHOLDERS


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 대상 고르기 (순수 함수)


@dataclass(frozen=True)
class Target:
    """태깅할 공고 하나. text 는 정리한 글(전화번호 가림)."""

    desertion_no: str
    input_hash: str
    text: str
    tasks: tuple[str, ...]

    @classmethod
    def from_dict(cls, d: dict) -> Target:
        return cls(str(d["desertion_no"]), d["input_hash"], d["text"], tuple(d["tasks"]))


def is_done(log_row: dict | None) -> bool:
    """끝난 처리: OK·NO_RESULT·NO_TEXT, 또는 FAILED 를 MAX_ATTEMPTS 번 채움."""
    if not log_row:
        return False
    if log_row.get("status") in DONE_STATUSES:
        return True
    return log_row.get("status") == FAILED and int(log_row.get("attempts") or 0) >= MAX_ATTEMPTS


def key_from_secret(raw: str) -> str | None:
    """Secrets Manager 값 → 키. 값 자체가 키이거나, JSON 이면 SECRET_FIELDS 중 하나(필드가 하나뿐이면 그 값)."""
    try:
        data = json.loads(raw)
    except ValueError:
        return raw.strip() or None
    if isinstance(data, str):
        return data.strip() or None
    if isinstance(data, dict):
        found = next((str(data[f]).strip() for f in SECRET_FIELDS if data.get(f)), None)
        if found is None and len(data) == 1:
            found = str(next(iter(data.values())) or "").strip() or None
        return found
    return None


def read_secret(secret_id: str, profile: str | None = None) -> str:
    import os

    import boto3

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    region = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")  # 임베딩 키(gemini.py)와 같은 규칙
    client = session.client("secretsmanager", region_name=region)
    try:
        raw = client.get_secret_value(SecretId=secret_id)["SecretString"]
    except Exception as exc:  # 자격 만료·권한 없음·이름 없음을 한 메시지로 (키 값은 넣지 않음)
        raise RuntimeError(f"Secrets Manager {secret_id} 를 읽지 못했습니다: {type(exc).__name__}") from exc
    key = key_from_secret(raw)
    if not key:
        raise RuntimeError(f"Secrets Manager {secret_id} 에 키가 없습니다")
    return key


def load_jev_key(source: str = "auto", *, profile: str | None = None, environ=None,
                 env_loader=load_key, secret_reader=read_secret) -> tuple[str, str]:
    """(키, 어디서 읽었는지). auto = 환경변수·.env 에 있으면 그것, 없으면 Secrets Manager. env·secrets 는 그곳만."""
    import os

    if source not in KEY_SOURCES:
        raise ValueError(f"--key-source 는 {KEY_SOURCES} 중 하나: {source}")
    if source in ("auto", "env"):
        try:
            return env_loader(KEY_ENV), f"환경변수·.env {KEY_ENV}"
        except RuntimeError:
            if source == "env":
                raise
    secret_id = (environ if environ is not None else os.environ).get(SECRET_ID_ENV) or DEFAULT_SECRET_ID
    return secret_reader(secret_id, profile), f"Secrets Manager {secret_id}"


def runtime_config(task: str) -> dict:
    """레지스트리에 등록한 설정과 맞춰 보는 값 (scripts/serve/legacy_version_config.py 의 글 태그 설정과 같다)."""
    return {"model_id": MODEL, "prompt_version": VERSIONS[task]}


def registered_versions(values: Sequence[str] = ()) -> dict[str, str]:
    """--version 값들 → 작업별 등록 버전. 안 준 작업은 REGISTERED. 예: text_personality@2 (작업마다 한 번)."""
    versions = dict(REGISTERED)
    given: set[str] = set()
    for value in values:
        task, sep, n = value.partition("@")
        if task not in REGISTERED or not sep or not n.isdigit() or task in given:
            raise ValueError(f"--version 은 작업마다 한 번, {tuple(REGISTERED)} 중 하나의 task@N: {value}")
        given.add(task)
        versions[task] = value
    return versions


def _key(no: str, task: str, h: str, versions: Mapping[str, str] = REGISTERED) -> tuple:
    return log_key(SUBJECT_ANIMAL, no, task, versions[task], h)


def select_targets(notices: Iterable[dict], log_rows: Iterable[dict], *, species: Sequence[str] = DEFAULT_SPECIES,
                   limit: int | None = None, versions: Mapping[str, str] = REGISTERED) -> tuple[list[Target], list[str]]:
    """(부를 공고, 새로 NO_TEXT 로 적을 공고). 공고번호 순이라 다시 돌려도 같다."""
    done = log_index(log_rows)
    want = {str(s) for s in species}
    targets: list[Target] = []
    no_text: list[str] = []
    seen: set[str] = set()
    for r in sorted(notices, key=lambda r: str(r["desertion_no"])):
        no = str(r["desertion_no"])
        if no in seen or r.get("status_group") != PROTECTING or str(r.get("species_code")) not in want:
            continue
        seen.add(no)
        text = clean_text(r.get("info_text"), r.get("special_mark"))
        if is_blank(text):
            if not all(is_done(done.get(_key(no, t, NO_TEXT_HASH, versions))) for t in TASKS):
                no_text.append(no)
            continue
        h = text_hash(text)
        tasks = tuple(t for t in TASKS if not is_done(done.get(_key(no, t, h, versions))))
        if tasks:
            targets.append(Target(no, h, text, tasks))
    if limit:
        targets = targets[:limit]
    return targets, no_text


def pending_targets(targets: Iterable[Target], log_rows: Iterable[dict], versions: Mapping[str, str] = REGISTERED) -> list[Target]:
    """목록을 만든 뒤 다른 실행이 끝낸 작업을 뺀다."""
    done = log_index(log_rows)
    out = []
    for t in targets:
        tasks = tuple(task for task in t.tasks if not is_done(done.get(_key(t.desertion_no, task, t.input_hash, versions))))
        if tasks:
            out.append(Target(t.desertion_no, t.input_hash, t.text, tasks))
    return out


# ---------------------------------------------------------------- 호출과 판정


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class TextOutcome:
    """같은 글(input_hash)을 쓰는 공고들의 결과. 요청은 1번."""

    targets: list[Target]
    statuses: dict[str, str] = field(default_factory=dict)  # 작업 → 상태
    tag_rows: list[dict] = field(default_factory=list)
    error: str | None = None
    request: dict | None = None


def request_row(target: Target, res: DecisionResult | None, *, status: str, error: Exception | None,
                requested_at: datetime, run_id: str) -> dict:
    return {
        "request_id": res.request_id if res else None,
        "subject_type": SUBJECT_ANIMAL,
        "subject_key": target.desertion_no,  # 같은 글을 쓰는 다른 공고는 태그 detail.request_id 로 잇는다
        "task": tt.REQUEST_TASK,
        "prompt_version": tt.REQUEST_VERSION,
        "run_no": 1,
        "requested_model": MODEL,
        "response_model": res.response_model if res else None,
        "provider": res.provider if res else None,
        "canonical_slug": None,
        "media_resolution": None,
        "temperature": None,
        "reasoning_effort": None,
        "finish_reason": None,
        "native_finish_reason": None,
        "prompt_tokens": res.input_tokens if res else None,
        "completion_tokens": res.output_tokens if res else None,
        "reasoning_tokens": None,
        "cost_usd": float(res.cost_usd) if res and res.cost_usd is not None else None,
        "latency_ms": res.latency_ms if res else None,
        "attempts": res.attempts if res else None,
        "status": status,
        "error_code": (getattr(error, "code", None) or type(error).__name__) if error else None,
        "raw_answer": json.dumps(res.answers, ensure_ascii=False)[:8000] if res else (str(error)[:500] if error else None),
        "requested_at": requested_at,
        "gold_run_id": run_id,
        "dt": requested_at.astimezone(UTC).strftime("%Y-%m-%d"),
    }


def tag_rows_for(target: Target, d: tt.Decision, request_id: str | None, run_id: str, now: datetime,
                 versions: Mapping[str, str] = REGISTERED) -> list[dict]:
    rows = []
    if tt.TASK_PERSONALITY in target.tasks:
        detail = tt.personality_detail(d, request_id, target.text)
        rows += [_tag_row(target, tt.TASK_PERSONALITY, tdef, p, detail, run_id, now, versions) for tdef, p in d.personality]
    if tt.TASK_HEALTH in target.tasks:
        rows.append(_tag_row(target, tt.TASK_HEALTH, d.health, d.health_prob, tt.health_detail(d, request_id, target.text),
                             run_id, now, versions))
    return rows


def _tag_row(target: Target, task: str, tdef, prob: float, detail: str, run_id: str, now: datetime,
             versions: Mapping[str, str] = REGISTERED) -> dict:
    return {
        "subject_type": SUBJECT_ANIMAL,
        "subject_key": target.desertion_no,
        "tag_code": tdef.code,
        "source": SOURCE,
        "tagger_version": f"{MODEL}@{VERSIONS[task]}",
        "task": task,
        "model": MODEL,
        "prompt_version": VERSIONS[task],
        "version": versions[task],
        "source_hash": target.input_hash,
        "searchable": tdef.searchable,
        "displayable": tdef.displayable,
        "rank": None,
        "similarity": round(prob * 100),  # 예 확률(성격)·1등 확률(건강) × 100
        "detail": detail,
        "gold_run_id": run_id,
        "gold_updated_at": now,
    }


def group_by_text(targets: Sequence[Target]) -> list[list[Target]]:
    groups: dict[str, list[Target]] = {}
    for t in targets:
        groups.setdefault(t.input_hash, []).append(t)
    return list(groups.values())


def tag_texts(groups: Sequence[list[Target]], client: JevClient, run_id: str, *, concurrency: int = 8,
              now: Callable[[], datetime] = _now, versions: Mapping[str, str] = REGISTERED,
              ) -> tuple[list[TextOutcome], BaseException | None]:
    """글마다 Jev 에 한 번 묻는다. 두 번째 값은 실행을 멈춰야 하는 오류(키·계정 오류) 또는 None.

    멈춘 뒤의 글은 결과에서 빼서 다음 실행에서 다시 한다. 이미 나간 요청은 요청 기록만 남긴다.
    """
    stop: list[BaseException] = []

    def one(group: list[Target]):
        at = now()
        if stop:
            return group, None, None, at, True
        try:
            return group, client.decide(group[0].text, tt.QUESTIONS), None, at, False
        except AccountError as exc:
            stop.append(exc)
            return group, None, exc, at, True
        except OpenRouterError as exc:
            return group, None, exc, at, False

    with ThreadPoolExecutor(max(1, concurrency)) as pool:
        results = list(pool.map(one, groups))

    outcomes: list[TextOutcome] = []
    for group, res, err, at, aborted in results:
        if aborted:
            if res is not None:
                outcomes.append(TextOutcome(group, request=request_row(group[0], res, status="OK", error=None,
                                                                       requested_at=at, run_id=run_id)))
            continue
        outcomes.append(_outcome(group, res, err, at, run_id, now(), versions))
    return outcomes, (stop[0] if stop else None)


def _outcome(group: list[Target], res: DecisionResult | None, err: Exception | None, at: datetime, run_id: str,
             ts: datetime, versions: Mapping[str, str] = REGISTERED) -> TextOutcome:
    decision = None
    status = "OK"
    if res is not None:
        try:
            decision = tt.decide(res.answers)
        except (KeyError, ValueError, TypeError) as exc:
            err, status = exc, "BAD_ANSWER"
    elif err is not None:
        status = "ERROR"
    o = TextOutcome(group, request=request_row(group[0], res, status=status, error=err, requested_at=at, run_id=run_id)
                    if (res is not None or isinstance(err, OpenRouterError)) else None)
    if decision is None:
        o.error = f"{type(err).__name__}: {err}"[:500] if err else "NO_ANSWER"
        o.statuses = {t: FAILED for t in TASKS}
        return o
    for t in group:
        o.tag_rows += tag_rows_for(t, decision, res.request_id, run_id, ts, versions)
    o.statuses = {tt.TASK_PERSONALITY: OK if decision.personality else NO_RESULT, tt.TASK_HEALTH: OK}
    return o


def inference_rows(outcomes: Sequence[TextOutcome], previous: dict[tuple, dict], run_id: str, ts: datetime,
                   versions: Mapping[str, str] = REGISTERED) -> list[dict]:
    rows = []
    for o in outcomes:
        if not o.statuses:
            continue
        for t in o.targets:
            for task in t.tasks:
                key = _key(t.desertion_no, task, t.input_hash, versions)
                rows.append({
                    "subject_type": SUBJECT_ANIMAL, "subject_key": t.desertion_no, "task": task, "version": versions[task],
                    "input_hash": t.input_hash, "status": o.statuses[task],
                    "attempts": int((previous.get(key) or {}).get("attempts") or 0) + 1,
                    "tag_count": sum(1 for r in o.tag_rows
                                     if r["subject_key"] == t.desertion_no and r["task"] == task and r["searchable"]),
                    "last_error": o.error, "gold_run_id": run_id, "gold_updated_at": ts,
                })
    return rows


def no_text_rows(nos: Iterable[str], run_id: str, ts: datetime, versions: Mapping[str, str] = REGISTERED) -> list[dict]:
    return [
        {"subject_type": SUBJECT_ANIMAL, "subject_key": no, "task": task, "version": versions[task], "input_hash": NO_TEXT_HASH,
         "status": NO_TEXT, "attempts": 1, "tag_count": 0, "last_error": None, "gold_run_id": run_id, "gold_updated_at": ts}
        for no in nos for task in TASKS
    ]


def replaced_tag_rows(outcomes: Sequence[TextOutcome], versions: Mapping[str, str] = REGISTERED,
                      ) -> tuple[list[tuple[str, str, str, str]], list[dict]]:
    """((공고번호, 작업, 버전, input_hash) 교체 범위, 새 태그 행).

    범위에 input_hash 가 들어 있어 이전 글의 행은 건드리지 않는다. FAILED 인 글은 범위에 넣지 않는다.
    """
    scope, rows = [], []
    for o in outcomes:
        for t in o.targets:
            for task in t.tasks:
                if o.statuses.get(task) in (OK, NO_RESULT):
                    scope.append((t.desertion_no, task, versions[task], t.input_hash))
        if any(s in (OK, NO_RESULT) for s in o.statuses.values()):
            rows.extend(o.tag_rows)
    return scope, rows


# ---------------------------------------------------------------- 저장소


class TextGoldSink:
    """Gold 태깅 묶음 표에 쓴다. 태그는 (text, animal, 공고번호, 작업, 버전, source_hash) 범위만 교체하고 이전 글·버전 행은 남긴다.
    처리 기록 upsert, 요청 기록 append."""

    def __init__(self, catalog, env, *, verify: bool = True, versions: Mapping[str, str] = REGISTERED):
        from rebom.common import gold_catalog as gc
        from rebom.common.gold_version_registry import (
            require_version_column,
            verify_runtime_config,
        )

        self.gc = gc
        self.tag = catalog.load_table(env.table(gc.TAG_TABLE))
        self.inference = catalog.load_table(env.table(gc.INFERENCE_LOG_TABLE))
        self.requests = catalog.load_table(env.table(gc.REQUEST_LOG_TABLE))
        require_version_column(self.tag)
        if verify:  # 등록하지 않았거나 설정이 다르면 Gold 에 쓰기 전에 멈춘다
            for task, version in versions.items():
                verify_runtime_config(catalog, env, task, version, runtime_config(task))

    def read_inference_log(self) -> list[dict]:
        """글 태깅 행만. 처리 기록은 사진 태깅·임베딩과 같이 쓰는 공용 로그다(2026-11-24, 읽기 조건에 task 를 넣는다)."""
        from pyiceberg.expressions import And, EqualTo, In

        rows = And(EqualTo("subject_type", SUBJECT_ANIMAL), In("task", list(TASKS)))
        return self.inference.scan(row_filter=rows).to_arrow().to_pylist()

    @staticmethod
    def _arrow(table, rows):
        import pyarrow as pa

        return pa.Table.from_pylist(rows, schema=table.schema().as_arrow())

    def write(self, scope, tag_rows, log_rows, request_rows) -> None:
        # 다른 작업(사진 태깅 등)이 같은 표에 쓴 뒤면 표 정보가 낡아 커밋이 충돌·재시도한다. 쓰기 직전에 다시 읽는다
        # (2026-11-24 MVP 적재: 재시도 뒤 Windows 에서 스택 넘침 0xC00000FD 로 조용히 종료됨)
        for table in (self.requests, self.tag, self.inference):
            table.refresh()
        if request_rows:
            self.requests.append(self._arrow(self.requests, request_rows))
        if scope:
            by_group: dict[tuple[str, str], list[tuple[str, str]]] = {}
            for no, task, version, h in scope:
                by_group.setdefault((task, version), []).append((no, h))
            for (task, version), pairs in by_group.items():
                rows = [r for r in tag_rows if r["task"] == task and r["version"] == version]
                self.tag.overwrite(self._arrow(self.tag, rows), overwrite_filter=tag_scope_filter(task, version, pairs))
        if log_rows:
            self.inference.upsert(self._arrow(self.inference, log_rows), join_cols=list(self.gc.INFERENCE_LOG_KEYS))


def tag_scope_filter(task: str, version: str, pairs: Sequence[tuple[str, str]]):
    """교체할 태그 행: source=text, subject_type=animal, 이 작업·등록 버전(version 칸), 그리고 (공고번호, source_hash) 쌍들.

    공고번호와 글 해시를 쌍으로 맞추므로 같은 공고의 이전 글 행, 사진 태그 행은 건드리지 않는다.
    """
    from pyiceberg.expressions import And, EqualTo, Or

    terms = [And(EqualTo("subject_key", no), EqualTo("source_hash", h)) for no, h in sorted(set(pairs))]
    which = terms[0] if len(terms) == 1 else Or(*terms)
    return And(
        And(And(EqualTo("source", SOURCE), EqualTo("subject_type", SUBJECT_ANIMAL)), EqualTo("task", task)),
        And(EqualTo("version", version), which),
    )


# ---------------------------------------------------------------- Silver 읽기·CLI


def read_silver(catalog, silver_env, cutoff=None, used: dict | None = None) -> list[dict]:
    """보호중 공고의 글 칸. info_text 가 없는 옛 스키마면 special_mark 만 읽는다.

    cutoff(run_photo_tags.silver_cutoff 값)를 주면 그 시각의 Silver 스냅샷을 읽는다(사진 태깅 --silver-as-of 와 같은 규칙).
    칸 유무도 그 스냅샷의 스키마로 판단한다. used 를 주면 읽은 snapshot_id·커밋 시각을 채운다(대상 목록에 남김).
    """
    from pyiceberg.expressions import EqualTo

    table = catalog.load_table(silver_env.notice_table)
    snapshot_id = _snapshot_id(table, silver_env.notice_table, cutoff, used)
    schema = table.schema()
    if snapshot_id is not None:
        snap = table.snapshot_by_id(snapshot_id)
        if snap is not None and snap.schema_id is not None:
            schema = table.schemas().get(snap.schema_id, schema)
    have = {f.name for f in schema.fields}
    cols = tuple(c for c in ("desertion_no", "status_group", "species_code", "info_text", "special_mark") if c in have)
    return table.scan(row_filter=EqualTo("status_group", PROTECTING), selected_fields=cols,
                      snapshot_id=snapshot_id).to_arrow().to_pylist()


def _clients(args):
    import boto3

    from rebom.storage.silver.aws_catalog import get_aws_catalog

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    return get_aws_catalog(profile=args.profile), session.client("s3", region_name="ap-northeast-2")


def work_prefix(gold_env: str) -> str:
    from rebom.common.gold_catalog import get_gold_environment

    return f"gold/_work{get_gold_environment(gold_env).suffix}/text_tags"


def _default_targets(args) -> str:
    if args.targets:
        return args.targets
    if not args.run_id:
        raise SystemExit("--targets 나 --run-id 중 하나가 필요합니다")
    return f"s3://{args.bucket}/{work_prefix(args.gold_env)}/{args.run_id}/targets.json"


def _sink(args, catalog, versions: Mapping[str, str] = REGISTERED):
    if args.local_out:
        return LocalSink(Path(args.local_out))
    from rebom.common.gold_catalog import get_gold_environment

    return TextGoldSink(catalog, get_gold_environment(args.gold_env), versions=versions)


def run_versions(given: Sequence[str], saved: Mapping[str, str] | None) -> dict[str, str]:
    """run 이 쓸 버전. 대상 목록에 적힌 버전(select 때 정함)을 따르고, --version 을 주면 그것과 같아야 한다.
    versions 가 없는 옛 목록은 REGISTERED(@1) 또는 --version 값."""
    versions = registered_versions(given)
    if saved:
        saved = dict(saved)
        if given and versions != saved:
            raise ValueError(f"대상 목록의 버전 {saved} 와 --version {versions} 이 다릅니다. 같은 버전으로 select 를 다시 하세요")
        return saved
    return versions


def cmd_select(args) -> int:
    from rebom.storage.silver.environment_config import get_silver_environment

    versions = registered_versions(getattr(args, "version", None) or ())
    catalog, s3 = _clients(args)
    sink = _sink(args, catalog, versions)
    cutoff = silver_cutoff(getattr(args, "silver_as_of", None))
    snapshots: dict = {}
    notices = read_silver(catalog, get_silver_environment(args.silver_env), cutoff, snapshots)
    species = tuple(s.strip() for s in args.species.split(",") if s.strip())
    targets, no_text = select_targets(notices, sink.read_inference_log(), species=species, limit=args.limit or None,
                                     versions=versions)
    texts = len({t.input_hash for t in targets})
    summary = {
        "run_id": args.run_id, "selected_at": _now().isoformat(), "silver_env": args.silver_env,
        "silver_as_of": cutoff[0].isoformat() if cutoff else None, "silver_snapshots": snapshots, "species": species,
        "protecting": len(notices), "targets": len(targets), "unique_texts": texts, "no_text_new": len(no_text),
        "prompt_versions": VERSIONS, "versions": versions, "items": [asdict(t) for t in targets],
    }
    for name, snap in snapshots.items():
        print(f"Silver {name}: snapshot {snap['snapshot_id']} ({snap['committed_at']})")
    print(f"보호중 공고 {len(notices)}건, 대상 {len(targets)}건(서로 다른 글 {texts}개 = 요청 {texts}번), 새 NO_TEXT {len(no_text)}건")
    if args.dry_run:
        print("dry-run: 목록·처리 기록을 쓰지 않습니다")
        return 0
    _write_json(_default_targets(args), summary, s3)
    if no_text:
        # 처리 기록만 남긴다. 글이 비게 된 공고의 이전 글 태그도 지우지 않는다(이전 버전 보존). 현재 태그 고르기에서 빠진다
        sink.write([], [], no_text_rows(sorted(no_text), args.run_id or "manual", _now(), versions), [])
    return 0


def cmd_run(args) -> int:
    catalog, s3 = _clients(args)
    data = _read_json(_default_targets(args), s3, args.bucket)
    versions = run_versions(getattr(args, "version", None) or (), data.get("versions"))
    sink = _sink(args, catalog, versions)
    targets = pending_targets([Target.from_dict(d) for d in data["items"]], sink.read_inference_log(), versions)
    groups = group_by_text(targets)
    if args.max_texts:
        groups = groups[: args.max_texts]
    print(f"버전 {versions}, 태깅할 공고 {sum(len(g) for g in groups)}건, 요청 {len(groups)}번")
    if args.dry_run or not groups:
        return 0
    key, where = load_jev_key(getattr(args, "key_source", "auto"), profile=args.profile)
    print(f"Jev 키: {where}")
    client = JevClient(KEY_ENV, key=key)
    run_id = args.run_id or f"manual-{_now():%Y%m%dT%H%M%SZ}"
    previous = log_index(sink.read_inference_log())
    totals = {"requests": 0, "cost": 0.0, OK: 0, NO_RESULT: 0, FAILED: 0}
    for start in range(0, len(groups), args.batch_size):
        batch = groups[start:start + args.batch_size]
        outcomes, stop = tag_texts(batch, client, run_id, concurrency=args.concurrency, versions=versions)
        ts = _now()
        scope, tag_rows = replaced_tag_rows(outcomes, versions)
        logs = inference_rows(outcomes, previous, run_id, ts, versions)
        reqs = [o.request for o in outcomes if o.request]
        sink.write(scope, tag_rows, logs, reqs)
        previous.update(log_index(logs))
        totals["requests"] += len(reqs)
        totals["cost"] += sum(r["cost_usd"] or 0 for r in reqs)
        for r in logs:
            totals[r["status"]] = totals.get(r["status"], 0) + 1
        print(f"{start + len(batch)}/{len(groups)}글 처리, 누적 비용 ${totals['cost']:.4f}, "
              f"처리 기록 OK {totals[OK]} · 성격 없음 {totals[NO_RESULT]} · 실패 {totals[FAILED]}")
        if stop is not None:
            print(f"중단: {stop}", file=sys.stderr)
            return 2
    print(f"끝: 요청 {totals['requests']}번, 비용 ${totals['cost']:.4f}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["select", "run"])
    ap.add_argument("--run-id", default=None, help="DAG 실행 id. --targets 를 비우면 목록 위치를 이 값으로 정한다")
    ap.add_argument("--targets", default=None, help="대상 목록 JSON (로컬 경로 또는 s3://)")
    ap.add_argument("--bucket", default="rebom-data-559987919299")
    ap.add_argument("--silver-env", default="test")
    ap.add_argument("--silver-as-of", default="", help="select: 이 날짜(그날 끝, KST)나 시각 기준 Silver 스냅샷으로 고른다. "
                    "비우면 지금 스냅샷. 예: 2026-11-23")
    ap.add_argument("--gold-env", default="test")
    ap.add_argument("--version", action="append", default=[], help="등록된 작업 버전, 예: text_personality@2. 작업별 반복 지정. "
                    "안 준 작업은 @1. run 은 대상 목록의 버전과 같아야 한다")
    ap.add_argument("--profile", default=None, help="AWS 프로필 (PC). 파드에서는 비운다")
    ap.add_argument("--local-out", default=None, help="Gold 대신 이 폴더에 JSONL 로 쓴다 (PC 시험용)")
    ap.add_argument("--species", default=",".join(DEFAULT_SPECIES), help="대상 축종 코드(쉼표). 기본 개·고양이")
    ap.add_argument("--limit", type=int, default=0, help="select: 대상 공고 상한 (시험용)")
    ap.add_argument("--max-texts", type=int, default=0, help="run: 이번 실행 요청(서로 다른 글) 상한. 0 이면 상한 없음")
    ap.add_argument("--batch-size", type=int, default=200, help="이 글 수마다 Gold 에 쓴다")
    ap.add_argument("--concurrency", type=int, default=8, help="동시 요청 수")
    ap.add_argument("--key-source", choices=KEY_SOURCES, default="auto",
                    help="run: Jev 키를 읽을 곳. auto=환경변수·.env 다음 Secrets Manager, env=환경변수·.env 만, "
                         "secrets=Secrets Manager 만(운영 적재)")
    ap.add_argument("--dry-run", action="store_true", help="대상만 세고 쓰거나 부르지 않는다")
    args = ap.parse_args(argv)
    return cmd_select(args) if args.command == "select" else cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
