"""동기화 계획: Silver·Gold 에서 읽은 행과 RDS 에 있는 행을 비교해 쓸 행·지울 행을 고른다.

DB·S3 를 부르지 않는 순수 함수만 둔다(테스트는 tests/test_serving_sync_plan.py). 규칙은 docs/data_contract.md 16.1.

- 공고(notice_status): 처음 보는 공고는 보호중일 때만 넣는다. RDS 에 보호중으로 있던 공고가 종료되면
  그 동기화의 Silver 수집 시각을 종료 전환 시각으로 두고 상세 노출은 30일 뒤까지(visible_until). 같은 종료를
  다시 반영해도 시각을 새로 잡지 않고, 보호중으로 돌아오면 둘 다 비운다. RDS 를 처음부터 채우므로(11/25 결정)
  "서비스 시작 전에 이미 종료된 공고는 제외"가 이 규칙으로 지켜진다.
- 사진(photo_vector): Silver 현재 사진 중 sha256 이 있는 칸만 표시한다. Silver 에 공고가 있는데 RDS 칸이 Silver 에
  없거나 sha256 이 비었으면(emptied) 그 칸을 지운다(벡터는 외래키로 같이 지워짐). Silver 에 공고 자체가 없으면 그대로 둔다.
- 사진 벡터: 활성 버전의 Gold 벡터 중 sha256 이 지금 칸의 사진과 같은 것만. 사진이 바뀌었는데 새 벡터가 아직 없으면
  옛 벡터를 지워 검색 후보에서 뺀다(사진은 바로 바뀐 것으로 보인다).
- 태그(tag): 사진 태그는 공고 대표 사진(키가 있는 가장 작은 칸)의 sha256, 글 태그는 그 공고·작업의 최신 입력 해시와
  맞는 Gold 행만.
- 글 벡터(text_vector): 공고마다 활성 버전의 Gold 행 중 가장 늦게 쓴 것 하나.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

VISIBLE_DAYS = 30  # 종료 공고 상세 노출 기간 (노션 '공고 노출 정책 · 확정')
PROTECTING = "PROTECTING"

# service_notice 의 공고 내용 칼럼 (003). Silver 공고 표 칼럼 이름과 같다. care_name·care_tel·care_address 는 보호소 표에서.
NOTICE_COLUMNS = (
    "desertion_no", "notice_no", "status_group", "process_state_raw", "closure_reason_code",
    "species_code", "species_name", "breed_code", "breed_name_raw", "breed_full_name_raw", "is_mix",
    "sex_code", "neuter_code", "birth_year", "weight_raw", "weight_kg", "size",
    "color_raw", "color_tag", "color_pattern", "special_mark", "sfe_soci", "sfe_health", "health_chk", "vaccination_chk",
    "happen_date", "happen_place", "org_name", "notice_start_date", "notice_end_date",
    "care_reg_no", "care_name", "care_tel", "care_address",
)
SHELTER_COLUMNS = ("care_name", "care_tel", "care_address")
SEX_CODES, NEUTER_CODES, SIZES = {"M", "F", "Q"}, {"Y", "N", "U"}, {"small", "medium", "large"}
STATUS_GROUPS = {"PROTECTING", "CLOSED", "UNKNOWN"}

_KEY_VERSION = re.compile(r"_v(\d{3})\.[A-Za-z0-9]+$")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _text(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _code(v: Any, allowed: set[str]) -> str | None:
    s = _text(v)
    return s.upper() if s and s.upper() in allowed else None


def _json_default(v: Any) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def canonical_hash(row: Mapping[str, Any], columns: Sequence[str]) -> str:
    body = json.dumps({c: row.get(c) for c in columns}, sort_keys=True, ensure_ascii=False, default=_json_default)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---- 공고 ---------------------------------------------------------------------------------------------------------

def notice_row(silver: Mapping[str, Any], shelter: Mapping[str, Any] | None) -> dict[str, Any]:
    """Silver 공고 행 + 보호소 행 → service_notice 내용 칼럼. 보호자 실명(careOwnerNm)은 Silver 에도 없고 여기도 없다."""
    no = _text(silver.get("desertion_no"))
    if not no:
        raise ValueError("desertion_no 없음")
    status = _text(silver.get("status_group")) or "UNKNOWN"
    if status not in STATUS_GROUPS:
        raise ValueError(f"status_group 오류: {no} {status!r}")
    row: dict[str, Any] = {c: silver.get(c) for c in NOTICE_COLUMNS if c not in SHELTER_COLUMNS}
    row.update(desertion_no=no, status_group=status,
               species_code=_text(silver.get("species_code")) or "",
               sex_code=_code(silver.get("sex_code"), SEX_CODES),
               neuter_code=_code(silver.get("neuter_code"), NEUTER_CODES),
               size=silver.get("size") if silver.get("size") in SIZES else None,
               care_reg_no=_text(silver.get("care_reg_no")))
    if row["weight_kg"] is not None:
        row["weight_kg"] = round(float(row["weight_kg"]), 2)
    if row["birth_year"] is not None:
        row["birth_year"] = int(row["birth_year"])
    if row["color_tag"] is not None:
        row["color_tag"] = list(row["color_tag"])
    for c in SHELTER_COLUMNS:
        row[c] = _text((shelter or {}).get(c))
    row["content_hash"] = canonical_hash(row, NOTICE_COLUMNS)
    return row


@dataclass
class NoticePlan:
    upserts: list[dict[str, Any]] = field(default_factory=list)
    unchanged: int = 0
    skipped_not_protecting: int = 0  # 처음 보는데 보호중이 아닌 공고 (넣지 않음)
    closed_now: int = 0  # 이번에 보호중 → 종료
    reopened: int = 0  # 종료 → 보호중

    @property
    def stats(self) -> dict[str, int]:
        return {"upserts": len(self.upserts), "unchanged": self.unchanged,
                "skipped_not_protecting": self.skipped_not_protecting,
                "closed_now": self.closed_now, "reopened": self.reopened}


def plan_notices(rows: Iterable[dict[str, Any]], existing: Mapping[str, Mapping[str, Any]],
                 observed_at: datetime, visible_days: int = VISIBLE_DAYS) -> NoticePlan:
    """rows: notice_row 결과. existing: RDS 공고번호 → {content_hash, status_group, closed_transition_at, visible_until}.
    observed_at: 이번 Silver 공고 완료본의 수집 시각(source_fetched_at)."""
    if observed_at.tzinfo is None:
        raise ValueError("observed_at 에 시간대가 없습니다")
    plan = NoticePlan()
    seen: set[str] = set()
    for row in rows:
        no = row["desertion_no"]
        if no in seen:
            raise ValueError(f"같은 공고가 두 번 있음: {no}")
        seen.add(no)
        old = existing.get(no)
        protecting = row["status_group"] == PROTECTING
        if old is None and not protecting:
            plan.skipped_not_protecting += 1
            continue
        closed_at, visible_until = None, None
        if not protecting and old is not None:
            if old.get("closed_transition_at") is not None:  # 같은 종료를 다시 반영: 처음 시각 유지
                closed_at, visible_until = old["closed_transition_at"], old.get("visible_until")
            elif old.get("status_group") == PROTECTING:  # 서비스가 본 보호중 → 종료 전환
                closed_at, visible_until = observed_at, observed_at + timedelta(days=visible_days)
                plan.closed_now += 1
        if protecting and old is not None and old.get("status_group") != PROTECTING:
            plan.reopened += 1
        new = {**row, "closed_transition_at": closed_at, "visible_until": visible_until}
        if old is not None and old.get("content_hash") == row["content_hash"] \
                and old.get("closed_transition_at") == closed_at and old.get("visible_until") == visible_until:
            plan.unchanged += 1
            continue
        plan.upserts.append(new)
    return plan


# ---- 사진 ---------------------------------------------------------------------------------------------------------

def photo_version(key: str) -> int:
    """원본·표시 키의 _v002 → 2, 없으면 1 (sources.photos_from_silver 와 같은 규칙)."""
    m = _KEY_VERSION.search(key or "")
    return int(m.group(1)) if m else 1


PHOTO_COMPARE = ("sha256", "photo_version", "serving_s3_key", "thumb_s3_key")  # RDS 칼럼과 같은 것만


def current_photos(silver_rows: Iterable[Mapping[str, Any]]) -> tuple[dict[tuple[str, int], dict[str, Any]], set[str]]:
    """Silver 현재 사진 표 행 → ((공고, 칸) → 표시 행, Silver 에 행이 있는 공고 집합). sha256·표시 키가 없는 칸은 표시하지 않는다."""
    photos: dict[tuple[str, int], dict[str, Any]] = {}
    present: set[str] = set()
    for r in silver_rows:
        no = _text(r.get("desertion_no"))
        slot = r.get("image_slot")
        if not no or slot is None or not 1 <= int(slot) <= 8:
            raise ValueError(f"공고번호·칸 오류: {no!r} {slot!r}")
        present.add(no)
        sha = (_text(r.get("sha256")) or "").lower()
        serving = _text(r.get("serving_s3_key"))
        if not sha or not serving:
            continue
        if not _SHA256.fullmatch(sha):
            raise ValueError(f"sha256 형식 오류: {no} {slot}")
        key = (no, int(slot))
        if key in photos:
            raise ValueError(f"같은 칸이 두 번 있음: {key}")
        original = _text(r.get("original_s3_key")) or ""
        photos[key] = {"desertion_no": no, "image_slot": int(slot), "sha256": sha,
                       "photo_version": photo_version(original or serving), "serving_s3_key": serving,
                       "thumb_s3_key": _text(r.get("thumb_s3_key")), "original_s3_key": original or None}
    return photos, present


@dataclass
class RowPlan:
    upserts: list[dict[str, Any]] = field(default_factory=list)
    deletes: list[tuple] = field(default_factory=list)
    unchanged: int = 0
    notes: dict[str, int] = field(default_factory=dict)

    @property
    def stats(self) -> dict[str, int]:
        return {"upserts": len(self.upserts), "deletes": len(self.deletes), "unchanged": self.unchanged, **self.notes}


def plan_photos(photos: Mapping[tuple[str, int], Mapping[str, Any]], present: set[str], notices: set[str],
                existing: Mapping[tuple[str, int], Mapping[str, Any]]) -> RowPlan:
    """notices: RDS service_notice 에 있는 공고(외래키). existing: RDS (공고, 칸) → PHOTO_COMPARE 값."""
    plan = RowPlan(notes={"no_notice": 0})
    for key, p in sorted(photos.items()):
        if key[0] not in notices:
            plan.notes["no_notice"] += 1
            continue
        old = existing.get(key)
        if old is not None and all(old.get(c) == p.get(c) for c in PHOTO_COMPARE):
            plan.unchanged += 1
            continue
        plan.upserts.append(dict(p))
    plan.deletes = sorted(k for k in existing if k[0] in present and k not in photos)
    return plan


def plan_image_vectors(photos: Mapping[tuple[str, int], Mapping[str, Any]], vectors: Mapping[str, Mapping[str, Any]],
                       existing: Mapping[tuple[str, int], str], version: str) -> RowPlan:
    """photos: 동기화 뒤 RDS 의 사진 칸(plan_photos 반영 뒤). vectors: sha256 → {embedding, gold_run_id}.
    existing: 이 버전의 RDS (공고, 칸) → sha256."""
    plan = RowPlan(notes={"waiting_vector": 0})
    for key, p in sorted(photos.items()):
        sha = p["sha256"]
        if existing.get(key) == sha:
            plan.unchanged += 1
            continue
        vec = vectors.get(sha)
        if vec is None:
            plan.notes["waiting_vector"] += 1
            if key in existing:  # 사진이 바뀌었는데 새 벡터가 없다: 옛 벡터를 검색에서 뺀다
                plan.deletes.append(key)
            continue
        plan.upserts.append({"desertion_no": key[0], "image_slot": key[1], "version": version,
                             "photo_version": p["photo_version"], "sha256": sha,
                             "s3_key": p.get("original_s3_key") or p["serving_s3_key"],
                             "gold_run_id": vec.get("gold_run_id"), "embedding": vec["embedding"]})
    plan.deletes += sorted(k for k in existing if k not in photos and k not in plan.deletes)
    return plan


# ---- 태그 ---------------------------------------------------------------------------------------------------------

TAG_KEY = ("desertion_no", "subject_type", "subject_key", "task", "tag_code")
TAG_COMPARE = ("tag_label", "version", "source", "searchable", "displayable", "rank", "similarity", "gold_run_id")


def representative(photos: Mapping[tuple[str, int], Mapping[str, Any]]) -> dict[str, str]:
    """공고 → 대표 사진 sha256 (키가 있는 가장 작은 칸, data_contract 13.1)."""
    rep: dict[str, tuple[int, str]] = {}
    for (no, slot), p in photos.items():
        if no not in rep or slot < rep[no][0]:
            rep[no] = (slot, p["sha256"])
    return {no: sha for no, (_, sha) in rep.items()}


def tag_label(code: str, labels: Mapping[str, str]) -> str:
    if code in labels:
        return labels[code]
    if code.startswith("breed."):
        return code[len("breed."):]
    return code


def plan_tags(notices: set[str], rep_photo: Mapping[str, str], text_inputs: Mapping[tuple[str, str], str],
              gold_rows: Iterable[Mapping[str, Any]], active: Mapping[str, str], labels: Mapping[str, str],
              existing: Mapping[tuple, Mapping[str, Any]]) -> RowPlan:
    """gold_rows: 활성 버전으로 거른 Gold 태그 행. text_inputs: (공고, 작업) → 최신 입력 해시.
    active: 작업 → 활성 버전(RDS 행의 version). existing: RDS TAG_KEY → TAG_COMPARE 값."""
    by_sha: dict[str, list[str]] = {}
    for no, sha in rep_photo.items():
        if no in notices:
            by_sha.setdefault(sha, []).append(no)
    want: dict[tuple, dict[str, Any]] = {}
    skipped = {"not_current": 0, "cute_blocked": 0}
    for g in gold_rows:
        task = g["task"]
        if task not in active:
            continue
        if g["subject_type"] == "photo":
            owners = by_sha.get(g["subject_key"], [])
        elif g["subject_type"] == "animal":
            no = g["subject_key"]
            owners = [no] if no in notices and text_inputs.get((no, task)) == g.get("source_hash") else []
        else:
            owners = []
        if not owners:
            skipped["not_current"] += 1
            continue
        searchable = bool(g.get("searchable"))
        if g["tag_code"] in ("cute", "impression.cute") and searchable:  # 귀염은 검색 태그가 될 수 없다(003 CHECK)
            searchable = False
            skipped["cute_blocked"] += 1
        for no in owners:
            row = {"desertion_no": no, "subject_type": g["subject_type"], "subject_key": g["subject_key"],
                   "task": task, "tag_code": g["tag_code"], "tag_label": tag_label(g["tag_code"], labels),
                   "version": active[task], "source": g["source"], "searchable": searchable,
                   "displayable": bool(g.get("displayable")), "rank": g.get("rank"), "similarity": g.get("similarity"),
                   "gold_run_id": g.get("gold_run_id")}
            key = tuple(row[c] for c in TAG_KEY)
            if key in want:
                raise ValueError(f"같은 태그가 두 번 있음: {key}")
            want[key] = row
    plan = RowPlan(notes=skipped)
    for key, row in sorted(want.items()):
        old = existing.get(key)
        if old is not None and all(old.get(c) == row[c] for c in TAG_COMPARE):
            plan.unchanged += 1
            continue
        plan.upserts.append(row)
    plan.deletes = sorted(k for k in existing if k not in want)
    return plan


# ---- 검색 질의 설정 --------------------------------------------------------------------------------------------------

QUERY_KEYS = ("model_id", "dim")


def query_config(config: Mapping[str, Any]) -> dict[str, Any] | None:
    """활성 벡터 버전의 레지스트리 설정 → active_version.query_config. 검색 API 는 사용자 입력을 이 모델·차원으로
    임베딩해 같은 버전의 저장 벡터와 비교한다. 레지스트리에 값이 없으면 None(검색 API 는 벡터 검색을 하지 않는다)."""
    if not all(config.get(k) for k in QUERY_KEYS):
        return None
    return {"model_id": str(config["model_id"]), "dim": int(config["dim"])}


# ---- 글 벡터 ------------------------------------------------------------------------------------------------------

def plan_text_vectors(notices: set[str], gold_rows: Iterable[Mapping[str, Any]],
                      existing: Mapping[str, str], version: str) -> RowPlan:
    """gold_rows: 활성 버전의 Gold 글 벡터 행 {subject_key, source_hash, embedding, gold_run_id, gold_updated_at}.
    existing: 이 버전의 RDS 공고 → text_sha256."""
    latest: dict[str, Mapping[str, Any]] = {}
    for g in gold_rows:
        no = g["subject_key"]
        cur = latest.get(no)
        if cur is None or (g.get("gold_updated_at"), g["source_hash"]) > (cur.get("gold_updated_at"), cur["source_hash"]):
            latest[no] = g
    plan = RowPlan(notes={"no_notice": 0})
    for no, g in sorted(latest.items()):
        if no not in notices:
            plan.notes["no_notice"] += 1
            continue
        if existing.get(no) == g["source_hash"]:
            plan.unchanged += 1
            continue
        plan.upserts.append({"desertion_no": no, "text_kind": "card", "version": version,
                             "text_sha256": g["source_hash"], "gold_run_id": g.get("gold_run_id"),
                             "embedding": g["embedding"]})
    plan.deletes = sorted((no,) for no in existing if no not in notices)
    return plan
