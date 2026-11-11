"""사진 Manifest 처리 이력 관리."""

import json
from datetime import datetime, timezone
from pathlib import Path

from botocore.exceptions import ClientError


def make_photo_journal(
    run_id,
    manifest_key,
    manifest_status,
    applied_count,
    needs_review,
    failed_count=0,
):
    if not run_id or not manifest_key:
        raise ValueError("run_id 또는 manifest_key 누락")

    if manifest_status not in {"ok", "aborted"}:
        raise ValueError("완료 또는 중단된 Manifest만 기록할 수 있습니다")

    if type(applied_count) is not int or applied_count < 0:
        raise ValueError("applied_count 오류")

    if type(failed_count) is not int or failed_count < 0:
        raise ValueError("failed_count 오류")

    return {
        "run_id": run_id,
        "manifest_key": manifest_key,
        "manifest_status": manifest_status,
        "processing_status": (
            "APPLIED" if manifest_status == "ok"
            else "APPLIED_PARTIAL"
        ),
        "applied_count": applied_count,
        "failed_count": failed_count,
        "needs_review": bool(needs_review),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }


def save_photo_journal(path, journal):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        raise FileExistsError(f"이미 처리 이력이 존재합니다: {path}")

    with path.open("x", encoding="utf-8") as f:
        json.dump(journal, f, ensure_ascii=False, indent=2)


def load_photo_journal(path):
    path = Path(path)

    if not path.exists():
        return None

    return json.loads(path.read_text(encoding="utf-8"))


def load_photo_journal_s3(s3, bucket, key):
    try:
        result = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404"}:
            return None
        raise

    return json.loads(result["Body"].read())


def save_photo_journal_s3(s3, bucket, key, journal):
    payload = json.dumps(
        journal, ensure_ascii=False, indent=2
    ).encode("utf-8")

    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=payload,
            ContentType="application/json",
            IfNoneMatch="*",
        )
        return "CREATED"

    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code not in {"PreconditionFailed", "412"}:
            raise

    existing = load_photo_journal_s3(s3, bucket, key)

    if existing is None:
        raise RuntimeError("Journal 충돌 후 기존 기록 조회 실패")

    fields = (
        "run_id",
        "manifest_key",
        "manifest_status",
        "processing_status",
        "applied_count",
        "needs_review",
    )

    for field in fields:
        if existing.get(field) != journal.get(field):
            raise RuntimeError(
                f"기존 Journal 내용 불일치: {field}"
            )

    if (
        "failed_count" in existing
        and existing["failed_count"] != journal["failed_count"]
    ):
        raise RuntimeError("기존 Journal 실패 건수 불일치")

    return "ALREADY_EXISTS"
