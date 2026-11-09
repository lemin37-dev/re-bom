"""A팀 사진 Manifest의 in_progress 정체 여부를 판단한다."""

import json
import os
from datetime import datetime, timezone


def _parse_updated_at(value):
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        parsed = datetime.fromisoformat(
            value.strip().replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return None

    return parsed.astimezone(timezone.utc)


def evaluate_photo_staleness(
    s3,
    bucket,
    manifest_key,
    now=None,
    threshold_seconds=None,
):
    if threshold_seconds is None:
        threshold_seconds = int(
            os.getenv("REBOM_PHOTO_STALE_SECONDS", "600")
        )

    if threshold_seconds <= 0:
        raise ValueError("정체 기준은 0보다 커야 합니다")

    if now is None:
        now = datetime.now(timezone.utc)

    if now.tzinfo is None:
        raise ValueError("now에는 시간대 정보가 필요합니다")

    now = now.astimezone(timezone.utc)

    def read_manifest():
        raw = s3.get_object(
            Bucket=bucket,
            Key=manifest_key,
        )["Body"].read()
        return json.loads(raw)

    first = read_manifest()
    status = first.get("status")
    run_id = first.get("run_id")

    if status in {"ok", "aborted"}:
        return {
            "run_id": run_id,
            "decision": "READY_FINAL",
            "status": status,
            "reread": False,
        }

    if status != "in_progress":
        raise ValueError(f"알 수 없는 사진 상태: {status}")

    updated = _parse_updated_at(first.get("updated_at"))

    if updated is None:
        return {
            "run_id": run_id,
            "decision": "DEFER_INVALID_TIMESTAMP",
            "status": status,
            "reread": False,
        }

    age_seconds = (now - updated).total_seconds()

    if age_seconds < threshold_seconds:
        return {
            "run_id": run_id,
            "decision": "DEFER",
            "status": status,
            "age_seconds": age_seconds,
            "reread": False,
        }

    latest = read_manifest()

    if latest.get("run_id") != run_id:
        raise RuntimeError("재조회한 Manifest의 run_id가 다릅니다")

    latest_status = latest.get("status")

    if latest_status in {"ok", "aborted"}:
        return {
            "run_id": run_id,
            "decision": "READY_FINAL",
            "status": latest_status,
            "reread": True,
        }

    if latest_status != "in_progress":
        raise ValueError(
            f"재조회한 Manifest 상태 오류: {latest_status}"
        )

    latest_updated = _parse_updated_at(latest.get("updated_at"))

    if latest_updated is None:
        return {
            "run_id": run_id,
            "decision": "DEFER_INVALID_TIMESTAMP",
            "status": latest_status,
            "reread": True,
        }

    latest_age = (now - latest_updated).total_seconds()

    return {
        "run_id": run_id,
        "decision": (
            "STALE_REVIEW"
            if latest_age >= threshold_seconds
            else "DEFER"
        ),
        "status": latest_status,
        "age_seconds": latest_age,
        "reread": True,
    }
