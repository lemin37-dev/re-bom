"""운영 기록(RDS ops_run_summary · ops_failure · ops_schema_snapshot, 마이그레이션 008·009) 쓰기.

운영 모니터링 화면(/ops)의 숫자와 실패 원인의 출처다. 규칙:
- 기록은 본 작업을 멈추지 않는다. safe_write 는 예외를 삼키고 이유 문자열만 돌려준다(표가 아직 없어도, DB 가 안 닿아도).
- 같은 실행·단계를 다시 쓰면 덮어쓴다(요약은 UPSERT, 실패 목록은 그 단계 것을 지우고 다시 넣음). 재실행해도 두 벌이 되지 않는다.
- 값 목록은 008·009 의 CHECK 와 같다(docs/schema/rds.dbml Enum ops_*). 목록 밖 값은 쓰기 전에 ValueError.
- evidence 에는 보호자 실명·연락처를 넣지 않는다. 혹시 들어와도 SENSITIVE_KEYS 는 지운다.
- Gold 모델 실패는 여기 쓰지 않는다. 화면이 gold_inference_log · gold_model_request_log 를 gold_run_id 로 바로 읽는다.

REBOM_OPS_LOG=off 면 아무것도 쓰지 않는다(로컬 시험용).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

ENVS = ("test", "prod")
STAGE = re.compile(r"^(bronze|photo|silver_notice|silver_photo|silver_publish|gold_[a-z_]+|rds_[a-z_]+)$")
STATUSES = ("ok", "warn", "failed", "skipped")
SUBJECT_TYPES = ("animal", "photo_slot", "partition", "route", "run")
# 008·009 의 CHECK 와 같은 순서·같은 값 (tests/test_ops_log.py 가 마이그레이션·DBML 과 비교)
REASON_CODES = (
    "aborted", "count_mismatch", "dropped_row", "not_found", "replaced_not_found", "gave_up",
    "download_failed", "raw_content_conflict", "silver_transform_conflict",
    "status_history_conflict", "partition_incomplete", "publish_blocked", "stale_input",
    "config_hash_mismatch", "no_active_version", "fk_skipped", "sync_error",
    # 009: 수집 API 오류 종류와 형식이 틀린 구간
    "api_result_error", "api_timeout", "api_http_error", "invalid_partition",
)
LAYERS = ("silver", "gold")
SENSITIVE_KEYS = frozenset({"careOwnerNm", "callName", "callTel", "care_owner_name", "owner_name", "owner_tel"})
MESSAGE_LIMIT = 500


def _check(value: Any, allowed: Sequence[str], name: str) -> None:
    if value not in allowed:
        raise ValueError(f"OPS_{name.upper()}: {value!r}")


def _iso(value: datetime | str | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    if value.tzinfo is None:
        raise ValueError("OPS_NAIVE_TIME: 시간대가 없는 시각")
    return value.isoformat()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scrub(value: Any) -> Any:
    """dict·list 안의 보호자 정보 키를 지운다."""
    if isinstance(value, Mapping):
        return {k: scrub(v) for k, v in value.items() if k not in SENSITIVE_KEYS}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


def stage_row(env: str, bronze_run_id: str, stage: str, status: str, *, started_at: datetime | str,
              finished_at: datetime | str | None = None, publish_id: str | None = None, gold_run_id: str | None = None,
              input_count: int | None = None, output_count: int | None = None, failed_count: int | None = None,
              stats: Mapping[str, Any] | None = None, code_version: str | None = None) -> dict[str, Any]:
    _check(env, ENVS, "env")
    _check(status, STATUSES, "status")
    if not STAGE.fullmatch(stage or ""):
        raise ValueError(f"OPS_STAGE: {stage!r}")
    if not bronze_run_id:
        raise ValueError("OPS_BRONZE_RUN_ID")
    return {
        "env": env, "bronze_run_id": bronze_run_id, "publish_id": publish_id, "gold_run_id": gold_run_id,
        "stage": stage, "status": status, "started_at": _iso(started_at), "finished_at": _iso(finished_at),
        "input_count": input_count, "output_count": output_count, "failed_count": failed_count,
        "stats": scrub(dict(stats or {})), "code_version": code_version,
    }


def failure_row(env: str, bronze_run_id: str, stage: str, subject_type: str, subject_key: str, reason_code: str, *,
                observed_at: datetime | str, message: str | None = None, attempts: int | None = None,
                evidence: Mapping[str, Any] | None = None, evidence_ref: str | None = None, blocking: bool = False,
                publish_id: str | None = None) -> dict[str, Any]:
    _check(env, ENVS, "env")
    _check(subject_type, SUBJECT_TYPES, "subject_type")
    _check(reason_code, REASON_CODES, "reason_code")
    if not STAGE.fullmatch(stage or ""):
        raise ValueError(f"OPS_STAGE: {stage!r}")
    if not bronze_run_id or not subject_key:
        raise ValueError("OPS_FAILURE_KEY")
    return {
        "env": env, "bronze_run_id": bronze_run_id, "publish_id": publish_id, "stage": stage,
        "subject_type": subject_type, "subject_key": str(subject_key), "reason_code": reason_code,
        "message": (message or None) and str(message)[:MESSAGE_LIMIT], "attempts": attempts,
        "evidence": scrub(dict(evidence or {})), "evidence_ref": evidence_ref, "blocking": bool(blocking),
        "observed_at": _iso(observed_at),
    }


def fingerprint(columns: Iterable[Sequence[str]]) -> str:
    """(칼럼 이름, 타입) 목록의 지문. 순서와 무관하게 같은 칼럼이면 같은 값 (화면의 문서 지문과 같은 방식)."""
    pairs = sorted((str(name), str(typ).lower()) for name, typ in columns)
    return hashlib.sha256(json.dumps(pairs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def schema_row(env: str, layer: str, table_name: str, physical_name: str, schema_id: int,
               columns: Sequence[Sequence[str]], *, observed_at: datetime | str,
               bronze_run_id: str | None = None) -> dict[str, Any]:
    _check(env, ENVS, "env")
    _check(layer, LAYERS, "layer")
    cols = [[str(n), str(t)] for n, t in columns]
    return {
        "env": env, "layer": layer, "table_name": table_name, "physical_name": physical_name,
        "schema_id": int(schema_id), "fingerprint": fingerprint(cols), "columns": cols,
        "observed_at": _iso(observed_at), "bronze_run_id": bronze_run_id,
    }


# ───────── DB 쓰기 ─────────

_SUMMARY_COLS = ("env", "bronze_run_id", "publish_id", "gold_run_id", "stage", "status", "started_at", "finished_at",
                 "input_count", "output_count", "failed_count", "stats", "code_version")
_FAILURE_COLS = ("env", "bronze_run_id", "publish_id", "stage", "subject_type", "subject_key", "reason_code", "message",
                 "attempts", "evidence", "evidence_ref", "blocking", "observed_at")
_SCHEMA_COLS = ("env", "layer", "table_name", "physical_name", "schema_id", "fingerprint", "columns", "observed_at",
                "bronze_run_id")
_JSON_COLS = {"stats", "evidence", "columns"}


def _values(row: Mapping[str, Any], cols: Sequence[str]) -> list[Any]:
    return [json.dumps(row[c], ensure_ascii=False, default=str) if c in _JSON_COLS else row[c] for c in cols]


def _placeholders(cols: Sequence[str]) -> str:
    return ", ".join("%s::jsonb" if c in _JSON_COLS else "%s" for c in cols)


SUMMARY_SQL = (
    f"INSERT INTO ops_run_summary ({', '.join(_SUMMARY_COLS)}) VALUES ({_placeholders(_SUMMARY_COLS)}) "
    "ON CONFLICT (env, bronze_run_id, stage) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in _SUMMARY_COLS if c not in ("env", "bronze_run_id", "stage"))
    + ", created_at = now()"
)
FAILURE_DELETE_SQL = "DELETE FROM ops_failure WHERE env = %s AND bronze_run_id = %s AND stage = %s"
FAILURE_SQL = f"INSERT INTO ops_failure ({', '.join(_FAILURE_COLS)}) VALUES ({_placeholders(_FAILURE_COLS)})"
SCHEMA_LATEST_SQL = ("SELECT fingerprint, schema_id FROM ops_schema_snapshot WHERE env = %s AND table_name = %s "
                     "ORDER BY observed_at DESC, id DESC LIMIT 1")
SCHEMA_SQL = f"INSERT INTO ops_schema_snapshot ({', '.join(_SCHEMA_COLS)}) VALUES ({_placeholders(_SCHEMA_COLS)})"


def write(conn, *, stages: Iterable[Mapping[str, Any]] = (), failures: Iterable[Mapping[str, Any]] = (),
          schemas: Iterable[Mapping[str, Any]] = (), replace_failures_for: Iterable[tuple[str, str, str]] = ()
          ) -> dict[str, int]:
    """한 트랜잭션으로 쓴다. 실패 목록은 (env, bronze_run_id, stage) 별로 지우고 다시 넣는다.

    replace_failures_for: 실패가 0건이 된 단계도 옛 실패를 지우도록 키를 따로 줄 수 있다(stages 의 키는 자동 포함).
    """
    stages, failures, schemas = list(stages), list(failures), list(schemas)
    keys = {(r["env"], r["bronze_run_id"], r["stage"]) for r in (*stages, *failures)} | set(replace_failures_for)
    out = {"stages": 0, "failures": 0, "schemas": 0}
    with conn.transaction():
        for row in stages:
            conn.execute(SUMMARY_SQL, _values(row, _SUMMARY_COLS))
            out["stages"] += 1
        for key in sorted(keys):
            conn.execute(FAILURE_DELETE_SQL, key)
        for row in failures:
            conn.execute(FAILURE_SQL, _values(row, _FAILURE_COLS))
            out["failures"] += 1
        for row in schemas:
            latest = conn.execute(SCHEMA_LATEST_SQL, (row["env"], row["table_name"])).fetchone()
            if latest and latest[0].strip() == row["fingerprint"] and int(latest[1]) == row["schema_id"]:
                continue  # 바뀌지 않았으면 쓰지 않는다
            conn.execute(SCHEMA_SQL, _values(row, _SCHEMA_COLS))
            out["schemas"] += 1
    return out


def enabled() -> bool:
    return os.environ.get("REBOM_OPS_LOG", "on").strip().lower() not in ("off", "0", "false", "no")


def safe_write(*, stages: Iterable[Mapping[str, Any]] = (), failures: Iterable[Mapping[str, Any]] = (),
               schemas: Iterable[Mapping[str, Any]] = (), replace_failures_for: Iterable[tuple[str, str, str]] = (),
               connect: Callable[[], Any] | None = None) -> dict[str, int] | str:
    """write 와 같지만 예외를 밖으로 내지 않는다. 성공이면 건수, 실패면 이유 문자열 (표준 오류에도 한 줄)."""
    if not enabled():
        return "OPS_LOG_DISABLED"
    try:
        if connect is None:
            from rebom.common.migrate import connect as _connect

            connect = _connect
        with connect() as conn:
            return write(conn, stages=stages, failures=failures, schemas=schemas,
                         replace_failures_for=replace_failures_for)
    except Exception as exc:  # noqa: BLE001 - 운영 기록 실패로 본 작업을 멈추지 않는다
        reason = f"{type(exc).__name__}: {str(exc)[:200]}"
        print(f"OPS_LOG_SKIPPED: {reason}", file=sys.stderr, flush=True)
        return reason
