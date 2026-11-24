"""Immutable S3 checkpoints for Silver run stages."""

import json

from botocore.exceptions import ClientError

from rebom.storage.silver.run_journal import STAGES, journal_path


def stage_key(prefix, run_id, stage):
    journal_path(run_id, ".")
    if stage not in STAGES:
        raise ValueError("Invalid stage")
    if not prefix or prefix.startswith("/"):
        raise ValueError("Invalid S3 prefix")

    return (
        f"{prefix.rstrip('/')}/run_id={run_id}/"
        f"stages/{STAGES.index(stage):02d}_{stage}.json"
    )


def read_stage(s3, bucket, prefix, run_id, stage):
    key = stage_key(prefix, run_id, stage)

    try:
        response = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404"}:
            return None
        raise

    record = json.loads(response["Body"].read())

    if record != {"run_id": run_id, "stage": stage}:
        raise RuntimeError(f"Invalid S3 checkpoint: {stage}")

    return record


def current_stage(s3, bucket, prefix, run_id):
    latest = None
    missing_seen = False

    for stage in STAGES:
        record = read_stage(s3, bucket, prefix, run_id, stage)

        if record is None:
            missing_seen = True
        else:
            if missing_seen:
                raise RuntimeError(
                    f"Missing preceding checkpoint: {stage}"
                )
            latest = stage

    return latest


def write_stage(s3, bucket, prefix, run_id, stage):
    key = stage_key(prefix, run_id, stage)
    index = STAGES.index(stage)

    latest = current_stage(s3, bucket, prefix, run_id)

    if latest is not None:
        latest_index = STAGES.index(latest)

        if index <= latest_index:
            existing = read_stage(
                s3, bucket, prefix, run_id, stage
            )
            if existing is None:
                raise RuntimeError("Existing checkpoint missing")
            return "ALREADY_EXISTS"

        if index != latest_index + 1:
            raise ValueError("Cannot skip checkpoint stages")
    elif stage != STAGES[0]:
        raise ValueError("First checkpoint must be PREPARED")

    payload = json.dumps(
        {"run_id": run_id, "stage": stage}
    ).encode("utf-8")

    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=payload,
            ContentType="application/json",
            IfNoneMatch="*",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")

        if code not in {"PreconditionFailed", "412"}:
            raise

        existing = read_stage(
            s3, bucket, prefix, run_id, stage
        )
        if existing != {"run_id": run_id, "stage": stage}:
            raise RuntimeError(
                f"Conflicting S3 checkpoint: {stage}"
            ) from exc

        return "ALREADY_EXISTS"

    return "CREATED"


from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4


@contextmanager
def silver_writer_lock(s3, bucket, environment, run_id):
    if environment not in ("test", "prod"):
        raise ValueError("Invalid Silver environment")

    key = (
        "bench_tmp/b-storage-test/locks/notice-history.lock.json"
        if environment == "test"
        else "silver/rebom/locks/notice-history.lock.json"
    )

    record = {
        "run_id": run_id,
        "owner": uuid4().hex,
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        response = s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(record).encode("utf-8"),
            ContentType="application/json",
            IfNoneMatch="*",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {
            "PreconditionFailed", "412",
            "ConditionalRequestConflict", "409",
        }:
            raise RuntimeError(
                f"SILVER_WRITER_LOCKED: {key}"
            ) from exc
        raise

    etag = response["ETag"]
    print("S3_WRITER_LOCK: ACQUIRED", flush=True)

    try:
        yield
    finally:
        s3.delete_object(
            Bucket=bucket,
            Key=key,
            IfMatch=etag,
        )
        print("S3_WRITER_LOCK: RELEASED", flush=True)


def read_notice_snapshot(s3, bucket, prefix, run_id):
    journal_path(run_id, ".")

    key = f"{prefix.rstrip('/')}/run_id={run_id}/notice_snapshot.json"

    try:
        response = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404"}:
            return None
        raise

    record = json.loads(response["Body"].read())

    if (
        record.get("run_id") != run_id
        or not record.get("notice_table")
        or type(record.get("notice_snapshot_id")) is not int
        or record["notice_snapshot_id"] <= 0
    ):
        raise RuntimeError("INVALID_NOTICE_SNAPSHOT_EVIDENCE")

    return record


def save_notice_snapshot(
    s3, bucket, prefix, run_id, notice_table, snapshot_id
):
    journal_path(run_id, ".")

    if type(snapshot_id) is not int or snapshot_id <= 0:
        raise ValueError("Invalid snapshot ID")

    if not notice_table:
        raise ValueError("Notice table is required")

    key = f"{prefix.rstrip('/')}/run_id={run_id}/notice_snapshot.json"

    record = {
        "run_id": run_id,
        "notice_table": notice_table,
        "notice_snapshot_id": snapshot_id,
    }

    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(record).encode("utf-8"),
            ContentType="application/json",
            IfNoneMatch="*",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")

        if code not in {"PreconditionFailed", "412"}:
            raise

        existing = read_notice_snapshot(s3, bucket, prefix, run_id)

        if existing != record:
            raise RuntimeError("NOTICE_SNAPSHOT_CONFLICT") from exc

        return "ALREADY_EXISTS"

    return "CREATED"
