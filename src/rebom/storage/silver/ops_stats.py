"""Silver 공고 처리 건수를 운영 기록용 JSON 으로 S3 에 남긴다 (운영 모니터링 화면의 출처).

지금 Silver 의 insert·update·충돌 수와 상태 쌍별 건수는 실행 중 메모리에만 있다. 준비(prepare_silver_pipeline)가 끝난
직후, 충돌로 멈추기 전에 이 파일을 쓴다. 저장 이미지(storage)에는 DB 드라이버가 없어서 RDS 에 직접 쓰지 않고,
DAG 끝의 요약 태스크(rebom.common.ops_summary)가 이 파일을 읽어 RDS 운영 기록에 옮긴다.

  s3://<버킷>/ops/<env>/silver_notice/<bronze_run_id>.json   (같은 실행을 다시 돌리면 덮어씀)

공고 내용 값은 넣지 않는다(공고번호·충돌한 목록 이름·건수만). 쓰기가 실패해도 Silver 처리는 그대로 진행한다.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

PREFIX = "ops"
PARSE_FIELDS = ("weight_parse_status", "color_parse_status")


def stats_key(env: str, run_id: str) -> str:
    return f"{PREFIX}/{env}/silver_notice/{run_id}.json"


def build_notice_stats(prepared: Mapping[str, Any], previous_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """prepare_silver_pipeline 결과를 건수로 줄인다. 공고 값은 담지 않는다."""
    out: dict[str, Any] = {
        "run_id": prepared.get("run_id"),
        "ready": bool(prepared.get("ready")),
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if not out["ready"]:
        out["stage"] = prepared.get("stage")
        out["conflicts"] = [
            {"desertion_no": c.get("desertion_no"), "sources": list(c.get("sources") or []),
             "reason": c.get("reason")}
            for c in prepared.get("conflicts") or []
        ]
        return out
    previous = {row.get("desertion_no") for row in previous_rows}
    delta = prepared.get("notice_delta_rows") or []
    inserted = sum(1 for row in delta if row.get("desertion_no") not in previous)
    history = prepared.get("history_rows") or []
    transitions = Counter(
        f"{row.get('previous_state_raw') or '(없음)'} → {row.get('process_state_raw') or '(없음)'}"
        for row in history if row.get("event_type") != "INITIAL"
    )
    out.update({
        "insert_count": inserted,
        "update_count": len(delta) - inserted,
        "history_count": len(history),
        "transitions": dict(transitions.most_common()),
        "actual_closed_count": len(prepared.get("actual_closed_ids") or []),
        "inferred_closed_count": len(prepared.get("inferred_closed_ids") or []),
        "lookup_deferred_count": len(prepared.get("lookup_deferred_ids") or []),
        "event_conflict_count": len(prepared.get("event_conflicts") or []),
        "parse_hold": {f: dict(Counter(row.get(f) for row in delta if row.get(f))) for f in PARSE_FIELDS},
    })
    return out


def save_notice_stats_s3(s3, bucket: str, env: str, stats: Mapping[str, Any]) -> str | None:
    """S3 에 쓴다. 실패해도 예외를 내지 않고 이유를 돌려준다."""
    try:
        run_id = stats.get("run_id")
        if not run_id:
            raise ValueError("RUN_ID_MISSING")
        s3.put_object(Bucket=bucket, Key=stats_key(env, run_id),
                      Body=json.dumps(stats, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                      ContentType="application/json")
        return None
    except Exception as exc:  # noqa: BLE001 - 운영 기록 실패로 Silver 를 멈추지 않는다
        reason = f"{type(exc).__name__}: {str(exc)[:200]}"
        print(f"OPS_STATS_SKIPPED: {reason}", file=sys.stderr, flush=True)
        return reason


def reporter(s3, bucket: str, env: str):
    """prepare_new 에 넘길 콜백. 건수를 만들다 실패해도 Silver 를 멈추지 않는다."""

    def report(prepared, previous_rows):
        try:
            stats = build_notice_stats(prepared, previous_rows)
        except Exception as exc:  # noqa: BLE001
            print(f"OPS_STATS_SKIPPED: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr, flush=True)
            return
        save_notice_stats_s3(s3, bucket, env, stats)

    return report
