"""Bronze ?? ID ?? ?? Manifest ?? ? ?? ??."""

import json
import re
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from rebom.storage.silver.photo_processing_journal import (
    load_photo_journal_s3,
)


TEST_JOURNAL_PREFIX = (
    "bench_tmp/b-storage-test/journals/photo_auto_runs"
)

RUN_ID_PATTERN = re.compile(r"\d{8}T\d{6}-[0-9a-f]+")


def validate_target(journal_prefix, bronze_run_id=None):
    prefix = journal_prefix.rstrip("/")

    if prefix != TEST_JOURNAL_PREFIX:
        raise ValueError("PHOTO_PENDING_TEST_PREFIX_ONLY")

    if bronze_run_id is not None:
        if not RUN_ID_PATTERN.fullmatch(bronze_run_id):
            raise ValueError("INVALID_BRONZE_RUN_ID")

    return prefix


def pending_key(journal_prefix, bronze_run_id):
    prefix = validate_target(journal_prefix, bronze_run_id)
    return f"{prefix}/pending_bronze/{bronze_run_id}.json"


def resolved_key(journal_prefix, bronze_run_id):
    prefix = validate_target(journal_prefix, bronze_run_id)
    return f"{prefix}/resolved_bronze/{bronze_run_id}.json"


def _write_once(s3, bucket, key, record, identity_fields):
    payload = json.dumps(
        record, ensure_ascii=False, indent=2
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
        raise RuntimeError("PHOTO_PENDING_JOURNAL_MISSING")

    for field in identity_fields:
        if existing.get(field) != record.get(field):
            raise RuntimeError(
                f"PHOTO_PENDING_JOURNAL_CONFLICT: {field}"
            )

    return "ALREADY_EXISTS"


def save_pending_manifest(s3, bucket, journal_prefix, bronze_run_id):
    key = pending_key(journal_prefix, bronze_run_id)
    completion = resolved_key(journal_prefix, bronze_run_id)

    if load_photo_journal_s3(s3, bucket, completion) is not None:
        return "ALREADY_RESOLVED"

    record = {
        "bronze_run_id": bronze_run_id,
        "status": "PENDING",
        "reason": "PHOTO_MANIFEST_NOT_FOUND",
        "first_seen_at": datetime.now(timezone.utc).isoformat(),
    }

    return _write_once(
        s3,
        bucket,
        key,
        record,
        ("bronze_run_id", "status", "reason"),
    )


def list_pending_manifests(s3, bucket, journal_prefix):
    prefix = validate_target(journal_prefix)
    scan_prefix = f"{prefix}/pending_bronze/"
    pending = []

    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(
        Bucket=bucket,
        Prefix=scan_prefix,
    ):
        for item in page.get("Contents", []):
            key = item["Key"]

            if not key.endswith(".json"):
                continue

            bronze_run_id = key[len(scan_prefix):-5]
            expected = pending_key(prefix, bronze_run_id)

            if key != expected:
                raise RuntimeError("PHOTO_PENDING_KEY_MISMATCH")

            record = load_photo_journal_s3(s3, bucket, key)

            if (
                record is None
                or record.get("bronze_run_id") != bronze_run_id
                or record.get("status") != "PENDING"
            ):
                raise RuntimeError("PHOTO_PENDING_RECORD_INVALID")

            completion = resolved_key(prefix, bronze_run_id)

            if load_photo_journal_s3(s3, bucket, completion) is None:
                pending.append(bronze_run_id)

    return sorted(set(pending))


def mark_pending_resolved(
    s3, bucket, journal_prefix, bronze_run_id, photo_run_ids
):
    key = pending_key(journal_prefix, bronze_run_id)

    if load_photo_journal_s3(s3, bucket, key) is None:
        return "NOT_PENDING"

    if not photo_run_ids:
        raise ValueError("PHOTO_RUN_IDS_REQUIRED")

    run_ids = sorted(set(photo_run_ids))

    if any(not RUN_ID_PATTERN.fullmatch(value) for value in run_ids):
        raise ValueError("INVALID_PHOTO_RUN_ID")

    record = {
        "bronze_run_id": bronze_run_id,
        "status": "RESOLVED",
        "photo_run_ids": run_ids,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }

    return _write_once(
        s3,
        bucket,
        resolved_key(journal_prefix, bronze_run_id),
        record,
        ("bronze_run_id", "status", "photo_run_ids"),
    )
