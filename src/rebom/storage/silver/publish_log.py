"""Silver snapshots publication readiness policy."""


def assess_publish_readiness(
    *,
    notice_completed,
    photo_completed,
    notice_snapshot_id,
    photo_snapshot_id,
    same_bronze_run,
    photo_needs_review,
):
    """Return (status, reasons). Does not write or publish data."""
    reasons = []

    if notice_completed is not True:
        reasons.append("NOTICE_NOT_COMPLETED")

    if photo_completed is not True:
        reasons.append("PHOTO_NOT_COMPLETED")

    if type(notice_snapshot_id) is not int or notice_snapshot_id <= 0:
        reasons.append("NOTICE_SNAPSHOT_MISSING")

    if type(photo_snapshot_id) is not int or photo_snapshot_id <= 0:
        reasons.append("PHOTO_SNAPSHOT_MISSING")

    if same_bronze_run is not True:
        reasons.append("BRONZE_RUN_MISMATCH")

    if photo_needs_review is not False:
        reasons.append("PHOTO_REVIEW_REQUIRED")

    return ("BLOCKED", reasons) if reasons else ("READY", [])


def build_publish_record(
    *,
    publish_id,
    bronze_run_id,
    notice_run_id,
    photo_run_id,
    notice_table,
    photo_table,
    notice_snapshot_id,
    photo_snapshot_id,
    status,
    notice_completed,
    photo_completed,
    same_bronze_run,
    photo_needs_review,
):
    """Build a Gold input record after readiness checks. No AWS writes."""
    assessed_status, reasons = assess_publish_readiness(
        notice_completed=notice_completed,
        photo_completed=photo_completed,
        notice_snapshot_id=notice_snapshot_id,
        photo_snapshot_id=photo_snapshot_id,
        same_bronze_run=same_bronze_run,
        photo_needs_review=photo_needs_review,
    )
    if status != "READY" or assessed_status != "READY":
        raise ValueError(f"PUBLISH_NOT_READY: {reasons}")

    for name, value in {
        "publish_id": publish_id,
        "bronze_run_id": bronze_run_id,
        "notice_run_id": notice_run_id,
        "photo_run_id": photo_run_id,
        "notice_table": notice_table,
        "photo_table": photo_table,
    }.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"INVALID_{name.upper()}")

    for value in (notice_snapshot_id, photo_snapshot_id):
        if type(value) is not int or value <= 0:
            raise ValueError("INVALID_SNAPSHOT_ID")

    return {
        "publish_id": publish_id,
        "bronze_run_id": bronze_run_id,
        "notice_run_id": notice_run_id,
        "photo_run_id": photo_run_id,
        "notice_table": notice_table,
        "photo_table": photo_table,
        "notice_snapshot_id": notice_snapshot_id,
        "photo_snapshot_id": photo_snapshot_id,
        "status": "READY",
    }


def save_publish_record_s3(s3, bucket, prefix, record):
    """Save an immutable READY record to S3."""
    import json
    import re
    from botocore.exceptions import ClientError

    publish_id = record["publish_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", publish_id):
        raise ValueError("INVALID_PUBLISH_ID")
    if record.get("status") != "READY":
        raise ValueError("PUBLISH_NOT_READY")
    if not prefix or prefix.startswith("/"):
        raise ValueError("INVALID_PUBLISH_PREFIX")

    key = f"{prefix.rstrip('/')}/{publish_id}.json"
    body = json.dumps(
        record, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")

    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            IfNoneMatch="*",
        )
        return "CREATED"

    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code not in {"PreconditionFailed", "412"}:
            raise

    existing = json.loads(
        s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    )

    if existing != record:
        raise RuntimeError("PUBLISH_RECORD_CONFLICT")

    return "ALREADY_EXISTS"



def load_publish_journals(
    s3, bucket, notice_prefix, photo_prefix,
    bronze_run_id, photo_run_id,
):
    """Read Silver completion evidence without modifying AWS."""
    import json

    notice_key = (
        f"{notice_prefix.rstrip('/')}/run_id={bronze_run_id}/"
        "stages/03_COMPLETED.json"
    )
    photo_key = f"{photo_prefix.rstrip('/')}/{photo_run_id}.json"

    notice = json.loads(
        s3.get_object(Bucket=bucket, Key=notice_key)["Body"].read()
    )
    photo = json.loads(
        s3.get_object(Bucket=bucket, Key=photo_key)["Body"].read()
    )

    if notice != {"run_id": bronze_run_id, "stage": "COMPLETED"}:
        raise RuntimeError("NOTICE_JOURNAL_MISMATCH")

    if photo.get("run_id") != photo_run_id:
        raise RuntimeError("PHOTO_JOURNAL_MISMATCH")

    return notice, photo



def verify_photo_bronze_run(s3, bucket, photo_journal, bronze_run_id):
    """Verify the photo Manifest belongs to the selected Bronze run."""
    import json

    manifest_key = photo_journal["manifest_key"]

    if not (
        manifest_key.startswith("bronze/rescue/image_runs/")
        and manifest_key.endswith(
            f"/run_id={photo_journal['run_id']}/manifest.json"
        )
    ):
        raise RuntimeError("PHOTO_MANIFEST_PATH_MISMATCH")

    manifest = json.loads(
        s3.get_object(Bucket=bucket, Key=manifest_key)["Body"].read()
    )

    if (
        manifest.get("run_id") != photo_journal["run_id"]
        or manifest.get("bronze_run_id") != bronze_run_id
    ):
        raise RuntimeError("PHOTO_BRONZE_RUN_MISMATCH")

    return True



def build_notice_snapshot_evidence(
    *, bronze_run_id, notice_table, notice_snapshot_id,
):
    """Build a notice snapshot evidence record without writing to S3."""
    if not isinstance(bronze_run_id, str) or not bronze_run_id.strip():
        raise ValueError("INVALID_BRONZE_RUN_ID")
    if not isinstance(notice_table, str) or not notice_table.strip():
        raise ValueError("INVALID_NOTICE_TABLE")
    if type(notice_snapshot_id) is not int or notice_snapshot_id <= 0:
        raise ValueError("INVALID_NOTICE_SNAPSHOT_ID")

    return {
        "bronze_run_id": bronze_run_id,
        "notice_table": notice_table,
        "notice_snapshot_id": notice_snapshot_id,
    }
