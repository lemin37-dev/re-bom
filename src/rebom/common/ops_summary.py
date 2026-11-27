"""하루 실행(bronze_run_id 하나)의 운영 기록을 모아 RDS 에 쓴다. DAG rescue_ingest 의 마지막 태스크 write_ops_summary.

    python -m rebom.common.ops_summary --run-id 20261127T030011-9c41a2 --env test            # 모아서 RDS 에 씀
    python -m rebom.common.ops_summary --run-id 20261127T030011-9c41a2 --env test --dry-run  # 쓰지 않고 JSON 만 출력

모으는 곳 (모두 이미 남는 기록이고, 없으면 그 단계는 skipped):
  bronze          bronze/rescue/raw/dt=/run_id=/manifest.json            구간별 total·received, API 오류 종류, 중단, 버린 행
                  state/rescue_state.json                                마지막 성공 실행(last_run_id)
  photo           bronze/rescue/image_runs/dt=/run_id=/manifest.json     bronze_run_id 가 같은 사진 실행의 저장·실패 칸
  silver_notice   ops/<env>/silver_notice/<run_id>.json                  Silver 가 멈추기 전에 남긴 건수·충돌 (storage/silver/ops_stats.py)
  silver_publish  <발행 prefix>/silver_<run_id>.json                     READY 발행 기록
  gold_<task>     Gold gold_run_input(publish_id) → gold_inference_log·gold_model_request_log 를 gold_run_id 로 집계
  스키마 관측      Glue 의 Silver·Gold 표 현재 schema-id·칼럼 → ops_schema_snapshot (바뀌었을 때만)
RDS 동기화 단계(rds_<route>)는 동기화가 끝날 때 스스로 쓴다(serving/sync/cli.py). Gold 실패 건은 복사하지 않는다.

모으다 한 곳이 실패해도 나머지는 쓰고, 운영 기록 쓰기가 실패해도 종료 코드는 0 이다(파이프라인을 멈추지 않음).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from rebom.common import ops_log

DATA_BUCKET = "rebom-data-559987919299"
RAW_PREFIX = "bronze/rescue/raw"
IMAGE_RUNS_PREFIX = "bronze/rescue/image_runs"
PUBLISH_PREFIX = "bench_tmp/b-storage-test/publish"  # DAG SILVER_TEST_PUBLISH_PREFIX 와 같음
KST = timezone(timedelta(hours=9))
RUN_ID = re.compile(r"^(\d{4})(\d{2})(\d{2})T\d{6}-[0-9a-f]{6}$")
PHOTO_REASONS = {"not_found", "replaced_not_found"}
STATE_KEY = "state/rescue_state.json"  # 수집 상태 파일 (rescue_incremental.STATE_KEY 와 같음)


def run_dt(run_id: str) -> str:
    m = RUN_ID.fullmatch(run_id or "")
    if not m:
        raise ValueError(f"INVALID_RUN_ID: {run_id!r}")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


# ───────── 단계별로 줄 만들기 (S3·Iceberg 없이 시험할 수 있는 순수 함수) ─────────

# totalCount 가 실제보다 1건 큰 날 (API 가 null 레코드를 세어서. 받은 쪽은 dropped_count 로 셈). 이 날만으로 어긋난
# count_mismatch 는 "알려진 예외"로 남기고 단계 상태를 warn 으로 만들지 않는다. 날짜를 더하면 근거를 같이 적는다.
KNOWN_COUNT_EXCEPTIONS = {
    "2023-02-11": "API null 레코드 때문에 totalCount 가 1건 큼",
    "2024-05-09": "API null 레코드 때문에 totalCount 가 1건 큼",
    "2025-09-09": "API null 레코드 때문에 totalCount 가 1건 큼",
}
API_ERROR_REASONS = {"timeout": "api_timeout", "http_error": "api_http_error", "result_code": "api_result_error"}
PARTITION_REASONS = {"api_result_error": "api_result_error", "invalid_date": "invalid_partition",
                     "invalid_date_range": "invalid_partition", "invalid_count": "invalid_partition"}


def known_exception(part: Mapping[str, Any]) -> str | None:
    """count_mismatch 구간의 차이가 구간 안 알려진 예외 날짜 수와 정확히 같으면 그 설명."""
    if part.get("reason") != "count_mismatch":
        return None
    days = [d for d in KNOWN_COUNT_EXCEPTIONS if str(part.get("start_date")) <= d <= str(part.get("end_date"))]
    try:
        diff = int(part.get("total_count")) - int(part.get("received_count"))
    except (TypeError, ValueError):
        return None
    if days and diff == len(days):
        return f"알려진 예외({', '.join(days)}): {KNOWN_COUNT_EXCEPTIONS[days[0]]}"
    return None


def _part_key(p: Mapping[str, Any]) -> str:
    return f"{p.get('kind')} {p.get('start_date')}~{p.get('end_date')}"


def bronze_rows(env: str, manifest: Mapping[str, Any], *, manifest_key: str | None = None,
                state: Mapping[str, Any] | None = None):
    """수집 manifest → bronze 단계 줄과 실패 줄.

    실패 줄: 불완전 구간(원인별), 재시도한 API 오류(종류별, 받아냈으면 evidence.recovered), 중단, 버린 항목.
    알려진 예외 구간과 재시도해서 받아낸 API 오류는 목록에는 남기되 단계 상태를 warn 으로 만들지 않는다.
    state: 수집 상태 파일(state/rescue_state.json). 마지막 성공 실행을 stats 에 남긴다.
    """
    run_id = manifest["run_id"]
    at = manifest.get("fetched_at") or ops_log.now()
    parts = manifest.get("partitions") or []
    files = manifest.get("files") or []
    failures, serious, known = [], 0, 0

    def add(row, counts=True):
        nonlocal serious
        failures.append(row)
        serious += int(counts)

    for p in parts:
        key = _part_key(p)
        evidence = {k: p.get(k) for k in ("kind", "start_date", "end_date", "result_code", "total_count",
                                          "received_count", "dropped_count", "complete", "reason")}
        if not p.get("complete", True):
            raw = p.get("reason")
            reason = raw if raw in ops_log.REASON_CODES else PARTITION_REASONS.get(raw, "count_mismatch")
            note = known_exception(p)
            known += int(bool(note))
            message = f"total {p.get('total_count')} / received {p.get('received_count')}"
            add(ops_log.failure_row(
                env, run_id, "bronze", "partition", key, reason, observed_at=at,
                message=f"{note} · {message}" if note else message,
                evidence={**evidence, "known_exception": bool(note), **({"known_reason": note} if note else {})},
                evidence_ref=manifest_key), counts=not note)
        for kind, n in (p.get("api_errors") or {}).items():
            codes = n if isinstance(n, Mapping) else None
            total = sum(int(v) for v in codes.values()) if codes else int(n)
            recovered = bool(p.get("complete", True))
            detail = f"resultCode {', '.join(f'{c}×{v}' for c, v in sorted(codes.items()))}" if codes else f"{total}번"
            add(ops_log.failure_row(
                env, run_id, "bronze", "partition", key, API_ERROR_REASONS.get(kind, "api_http_error"),
                observed_at=at, attempts=total,
                message=f"{detail} · {'재시도해서 받음' if recovered else '구간 불완전'}",
                evidence={"kind": p.get("kind"), "start_date": p.get("start_date"), "end_date": p.get("end_date"),
                          "error": kind, "count": total, "codes": codes, "recovered": recovered},
                evidence_ref=manifest_key), counts=not recovered)
    aborted = manifest.get("status") == "aborted"
    if aborted:
        add(ops_log.failure_row(
            env, run_id, "bronze", "run", run_id, "aborted", observed_at=at, blocking=True,
            message="; ".join(manifest.get("warnings") or [])[:300], evidence_ref=manifest_key))
    dropped = int(manifest.get("dropped_count") or 0)
    if dropped:
        # null 레코드(알려진 예외 날)도 여기에 세인다. 알려진 예외 구간 수만큼이면 warn 으로 보지 않는다
        add(ops_log.failure_row(
            env, run_id, "bronze", "run", run_id, "dropped_row", observed_at=at,
            message=f"버린 항목 {dropped}건" + (f" (알려진 예외 구간 {known}개 포함)" if known else ""),
            evidence={"dropped_count": dropped, "known_exception_partitions": known,
                      "known_exception": dropped <= known}, evidence_ref=manifest_key),
            counts=dropped > known)
    status = "failed" if aborted else ("warn" if serious else "ok")
    last = (state or {}).get("last_run_id")
    stage = ops_log.stage_row(
        env, run_id, "bronze", status, started_at=at,
        input_count=sum(int(p.get("total_count") or 0) for p in parts),
        output_count=sum(int(f.get("record_count") or 0) for f in files),
        failed_count=serious,
        stats={"mode": manifest.get("mode"), "calls": manifest.get("calls"), "run_status": manifest.get("status"),
               "duplicate_count": manifest.get("duplicate_count"), "dropped_count": dropped,
               "files": {f.get("kind"): f.get("record_count") for f in files},
               "incomplete_partitions": sum(1 for p in parts if not p.get("complete", True)),
               "known_exception_partitions": known, "api_errors": manifest.get("api_errors") or {},
               "last_success_run_id": last, "last_success_at": run_started_at(last),
               "last_enupd": (state or {}).get("last_enupd")},
        code_version=manifest.get("code_version"))
    return stage, failures


def run_started_at(run_id: str | None) -> str | None:
    """수집 run_id(YYYYMMDDTHHMMSS-xxxxxx, KST 시각)의 시작 시각. 형식이 다르면 None."""
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})-[0-9a-f]{6}", run_id or "")
    if not m:
        return None
    y, mo, d, h, mi, se = m.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{se}+09:00"


def photo_rows(env: str, manifest: Mapping[str, Any], *, manifest_key: str | None = None):
    run_id = manifest["bronze_run_id"]
    at = manifest.get("updated_at") or manifest.get("generated_at") or ops_log.now()
    failures = []
    for item in manifest.get("failed") or []:
        reason = item.get("reason")
        if reason not in PHOTO_REASONS:
            reason = "gave_up" if item.get("status") == "gave_up" else "download_failed"
        failures.append(ops_log.failure_row(
            env, run_id, "photo", "photo_slot", f"{item.get('desertionNo')}#{item.get('slot')}", reason,
            observed_at=at, attempts=item.get("attempts"), message=item.get("reason"),
            evidence={k: item.get(k) for k in ("desertionNo", "slot", "status", "reason", "attempts", "version")},
            evidence_ref=manifest_key))
    counts = manifest.get("counts") or {}
    status = {"aborted": "failed", "in_progress": "failed"}.get(manifest.get("status"), "warn" if failures else "ok")
    stage = ops_log.stage_row(
        env, run_id, "photo", status, started_at=manifest.get("generated_at") or at, finished_at=at,
        output_count=counts.get("stored"), failed_count=counts.get("failed", len(failures)),
        stats={"photo_run_id": manifest.get("run_id"), "counts": counts},
        code_version=manifest.get("code_version"))
    return stage, failures


def silver_notice_rows(env: str, run_id: str, stats: Mapping[str, Any], *, stats_key: str | None = None):
    at = stats.get("observed_at") or ops_log.now()
    failures = []
    if not stats.get("ready"):
        for c in stats.get("conflicts") or []:
            reason = c.get("reason")
            if stats.get("stage") == "STATUS_HISTORY" or reason not in ops_log.REASON_CODES:
                reason = "status_history_conflict"
            failures.append(ops_log.failure_row(
                env, run_id, "silver_notice", "animal", c.get("desertion_no") or run_id, reason, observed_at=at,
                blocking=True, message=f"sources: {', '.join(c.get('sources') or [])}",
                evidence={"sources": c.get("sources"), "stage": stats.get("stage"), "detail": c.get("reason")},
                evidence_ref=stats_key))
        stage = ops_log.stage_row(env, run_id, "silver_notice", "failed", started_at=at, failed_count=len(failures),
                                  stats={"stage": stats.get("stage")})
        return stage, failures
    holds = stats.get("parse_hold") or {}
    hold_total = sum(sum(v.values()) for v in holds.values())
    stage = ops_log.stage_row(
        env, run_id, "silver_notice", "warn" if hold_total else "ok", started_at=at,
        output_count=int(stats.get("insert_count") or 0) + int(stats.get("update_count") or 0), failed_count=0,
        stats={k: stats.get(k) for k in ("insert_count", "update_count", "history_count", "transitions",
                                         "actual_closed_count", "inferred_closed_count", "lookup_deferred_count",
                                         "event_conflict_count", "parse_hold")})
    return stage, failures


def publish_rows(env: str, run_id: str, record: Mapping[str, Any]):
    return ops_log.stage_row(
        env, run_id, "silver_publish", "ok", started_at=record.get("published_at") or ops_log.now(),
        publish_id=record.get("publish_id"),
        stats={k: record.get(k) for k in ("publish_seq", "notice_snapshot_id", "photo_snapshot_id", "photo_run_id",
                                          "notice_table", "photo_table")})


def gold_rows(env: str, run_id: str, run_inputs: Iterable[Mapping[str, Any]],
              inference: Iterable[Mapping[str, Any]], requests: Iterable[Mapping[str, Any]]):
    """gold_run_input 한 행 = 단계 하나. 처리 상태별 건수와 호출 수·비용·지연."""
    by_run: dict[str, Counter] = defaultdict(Counter)
    for row in inference:
        by_run[row.get("gold_run_id")][row.get("status") or "UNKNOWN"] += 1
    calls: dict[str, dict[str, Any]] = defaultdict(lambda: {"calls": 0, "errors": 0, "cost_usd": 0.0, "latency": [],
                                                              "models": Counter()})
    for row in requests:
        c = calls[row.get("gold_run_id")]
        c["calls"] += 1
        c["errors"] += int((row.get("status") or "OK") != "OK")
        c["cost_usd"] += float(row.get("cost_usd") or 0)
        if row.get("latency_ms") is not None:
            c["latency"].append(int(row["latency_ms"]))
        c["models"][f"{row.get('requested_model')} → {row.get('response_model')}"] += 1
    stages = []
    for ri in sorted(run_inputs, key=lambda r: str(r.get("started_at"))):
        gid, task = ri["gold_run_id"], ri["task"]
        counts, c = by_run.get(gid, Counter()), calls.get(gid)
        failed = sum(counts[s] for s in ("FAILED", "BAD_IMAGE", "READ_ERROR", "SHA_MISMATCH"))
        lat = sorted(c["latency"]) if c else []
        stats = {"version": ri.get("version"), "status_counts": dict(counts)}
        if c:
            stats.update({"calls": c["calls"], "call_errors": c["errors"], "cost_usd": round(c["cost_usd"], 6),
                          "latency_p50_ms": lat[len(lat) // 2] if lat else None,
                          "latency_p95_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None,
                          "models": dict(c["models"])})
        started = ri.get("started_at")
        stages.append(ops_log.stage_row(
            env, run_id, f"gold_{task}", "warn" if failed else "ok",
            started_at=started.isoformat() if hasattr(started, "isoformat") else (started or ops_log.now()),
            publish_id=ri.get("publish_id"), gold_run_id=gid, input_count=sum(counts.values()),
            output_count=counts.get("OK", 0), failed_count=failed, stats=stats))
    return stages


# ───────── 읽기 (S3 · Iceberg) ─────────

def _get_json(s3, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except Exception as exc:  # noqa: BLE001 - 없는 단계는 skipped
        if type(exc).__name__ not in ("NoSuchKey",) and "NoSuchKey" not in str(exc) and "404" not in str(exc):
            print(f"OPS_SUMMARY_READ: {key}: {type(exc).__name__}: {str(exc)[:160]}", file=sys.stderr)
        return None


def _photo_manifest(s3, bucket: str, run_id: str) -> tuple[dict[str, Any] | None, str | None]:
    prefix = f"{IMAGE_RUNS_PREFIX}/dt={run_dt(run_id)}/"
    best, best_key = None, None
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith("/manifest.json"):
                continue
            m = _get_json(s3, bucket, obj["Key"])
            if m and m.get("bronze_run_id") == run_id and (best is None or
                                                            str(m.get("updated_at")) > str(best.get("updated_at"))):
                best, best_key = m, obj["Key"]
    return best, best_key


def _skipped(env: str, run_id: str, stage: str, why: str) -> dict[str, Any]:
    return ops_log.stage_row(env, run_id, stage, "skipped", started_at=ops_log.now(), stats={"why": why})


def latest_manifest_run(s3, bucket: str, dt: str) -> str | None:
    """그날(dt=YYYY-MM-DD) 수집 manifest 중 가장 늦은 실행 id. 중단(aborted)된 실행은 XCom 을 넘기지 않아서 이것으로 찾는다."""
    best, best_at = None, ""
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=f"{RAW_PREFIX}/dt={dt}/"):
        for obj in page.get("Contents", []):
            m = re.search(r"/run_id=([^/]+)/manifest\.json$", obj["Key"])
            if m and RUN_ID.fullmatch(m.group(1)) and m.group(1)[:15] > best_at:  # 시작 시각(YYYYMMDDTHHMMSS)이 늦은 것
                best, best_at = m.group(1), m.group(1)[:15]
    return best


def collect_s3(s3, bucket: str, env: str, run_id: str, publish_prefix: str = PUBLISH_PREFIX):
    stages, failures = [], []
    key = f"{RAW_PREFIX}/dt={run_dt(run_id)}/run_id={run_id}/manifest.json"
    manifest = _get_json(s3, bucket, key)
    if manifest:
        st, fl = bronze_rows(env, manifest, manifest_key=key, state=_get_json(s3, bucket, STATE_KEY))
        stages.append(st)
        failures += fl
    else:
        stages.append(_skipped(env, run_id, "bronze", "manifest 없음"))
    photo, pkey = _photo_manifest(s3, bucket, run_id)
    if photo:
        st, fl = photo_rows(env, photo, manifest_key=pkey)
        stages.append(st)
        failures += fl
    else:
        stages.append(_skipped(env, run_id, "photo", "사진 manifest 없음"))
    from rebom.storage.silver.ops_stats import stats_key

    skey = stats_key(env, run_id)
    stats = _get_json(s3, bucket, skey)
    if stats:
        st, fl = silver_notice_rows(env, run_id, stats, stats_key=skey)
        stages.append(st)
        failures += fl
    else:
        stages.append(_skipped(env, run_id, "silver_notice", "Silver 건수 기록 없음"))
    record = _get_json(s3, bucket, f"{publish_prefix.rstrip('/')}/silver_{run_id}.json")
    stages.append(publish_rows(env, run_id, record) if record and record.get("status") == "READY"
                  else _skipped(env, run_id, "silver_publish", "READY 발행 없음"))
    return stages, failures


def collect_gold(catalog, gold_env, env: str, run_id: str, publish_id: str):
    from pyiceberg.expressions import EqualTo, In

    from rebom.common import gold_catalog as gc

    run_inputs = catalog.load_table(gold_env.table(gc.RUN_INPUT_TABLE)).scan(
        row_filter=EqualTo("publish_id", publish_id)).to_arrow().to_pylist()
    ids = sorted({r["gold_run_id"] for r in run_inputs})
    if not ids:
        return []
    inference = catalog.load_table(gold_env.table(gc.INFERENCE_LOG_TABLE)).scan(
        row_filter=In("gold_run_id", ids), selected_fields=("gold_run_id", "status")).to_arrow().to_pylist()
    requests = catalog.load_table(gold_env.table(gc.REQUEST_LOG_TABLE)).scan(
        row_filter=In("gold_run_id", ids),
        selected_fields=("gold_run_id", "status", "cost_usd", "latency_ms", "requested_model", "response_model"),
    ).to_arrow().to_pylist()
    return gold_rows(env, run_id, run_inputs, inference, requests)


def observed_tables(env: str, gold_env) -> list[tuple[str, str, str]]:
    """(층, 문서 표 이름, 실제 Glue 이름). Silver 는 environment_config, Gold 는 gold_catalog 기준."""
    from rebom.common import gold_catalog as gc
    from rebom.storage.silver.environment_config import get_silver_environment

    out = []
    silver = get_silver_environment(env)
    for doc, physical in (("silver_animal_notice", silver.notice_table),
                          ("silver_animal_status_history", silver.history_table),
                          ("silver_animal_image", silver.image_table)):
        out.append(("silver", doc, physical))
    for base in gc.schemas():
        out.append(("gold", base, gold_env.table(base)))
    return out


def collect_schemas(catalog, env: str, run_id: str, tables: Iterable[tuple[str, str, str]]):
    rows, at = [], ops_log.now()
    for layer, doc, physical in tables:
        try:
            schema = catalog.load_table(physical).schema()
        except Exception as exc:  # noqa: BLE001 - 없는 표는 건너뜀 (화면에서 "표 없음")
            print(f"OPS_SUMMARY_SCHEMA: {physical}: {type(exc).__name__}", file=sys.stderr)
            continue
        cols = [(f.name, str(f.field_type)) for f in schema.fields]
        rows.append(ops_log.schema_row(env, layer, doc, physical, schema.schema_id, cols, observed_at=at,
                                       bronze_run_id=run_id))
    return rows


def s3_client(profile: str | None = None):
    import boto3

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    return session.client("s3", region_name="ap-northeast-2")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True, help="Bronze 수집 실행 id")
    ap.add_argument("--env", default="test", choices=ops_log.ENVS, help="Silver 환경 (기록의 env)")
    ap.add_argument("--gold-env", default="test", choices=("test", "prod"))
    ap.add_argument("--bucket", default=DATA_BUCKET)
    ap.add_argument("--publish-prefix", default=PUBLISH_PREFIX)
    ap.add_argument("--skip-gold", action="store_true")
    ap.add_argument("--skip-schema", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="RDS 에 쓰지 않고 모은 줄만 출력")
    ap.add_argument("--profile", default=None, help="AWS 프로필 (PC). 파드는 노드 역할")
    ap.add_argument("--dt", default=None, help="실행 id 가 없을 때 manifest 를 찾을 수집일(YYYY-MM-DD, 기본 오늘 KST)")
    args = ap.parse_args(argv)

    s3 = s3_client(args.profile)
    if not RUN_ID.fullmatch(args.run_id or ""):
        # 수집이 중단(aborted)·실패하면 XCom 이 없어 "None" 이 온다. 그날 manifest 가 있으면(중단) 그 실행을 기록하고,
        # 없으면(수집이 manifest 도 못 쓰고 실패) 쓸 것이 없다. 화면은 그날 bronze 줄이 없으면 "실행 안 됨"으로 본다.
        dt = args.dt or datetime.now(KST).strftime("%Y-%m-%d")
        found = latest_manifest_run(s3, args.bucket, dt)
        if not found:
            print(json.dumps({"run_id": args.run_id, "dt": dt, "skipped": "수집 실행 id·manifest 없음"},
                             ensure_ascii=False))
            return 0
        args.run_id, args.skip_gold, args.skip_schema = found, True, True
    stages, failures = collect_s3(s3, args.bucket, args.env, args.run_id, args.publish_prefix)
    schemas, notes = [], []
    if not (args.skip_gold and args.skip_schema):
        try:
            from rebom.common.gold_catalog import get_gold_environment
            from rebom.storage.silver.aws_catalog import get_aws_catalog

            catalog, gold_env = get_aws_catalog(profile=args.profile), get_gold_environment(args.gold_env)
            if not args.skip_gold:
                stages += collect_gold(catalog, gold_env, args.env, args.run_id, f"silver_{args.run_id}")
            if not args.skip_schema:
                schemas = collect_schemas(catalog, args.env, args.run_id, observed_tables(args.env, gold_env))
        except Exception as exc:  # noqa: BLE001 - Gold·스키마를 못 읽어도 S3 쪽 기록은 쓴다
            notes.append(f"Gold·스키마 읽기 실패: {type(exc).__name__}: {str(exc)[:200]}")
    result: dict[str, Any] = {"run_id": args.run_id, "stages": len(stages), "failures": len(failures),
                              "schemas": len(schemas), "notes": notes}
    if args.dry_run:
        result.update({"stage_rows": stages, "failure_rows": failures, "schema_rows": schemas})
    else:
        result["written"] = ops_log.safe_write(stages=stages, failures=failures, schemas=schemas)
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
