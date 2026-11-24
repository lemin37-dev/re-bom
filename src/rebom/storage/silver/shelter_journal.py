"""??? ?? ?? ? S3 ?? ?? ??."""

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from botocore.exceptions import ClientError

from rebom.storage.silver.shelter_apply import run_time
from rebom.storage.silver.s3_run_snapshot import (
    read_object,
    sha256_bytes,
)

PREFIX = "bench_tmp/b-storage-test/shelter-journal"
LOCK_KEY = "bench_tmp/b-storage-test/locks/shelter.lock.json"


def encode(data):
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def read_json(s3, bucket, key):
    payload = read_object(s3, bucket, key)
    return json.loads(payload) if payload is not None else None


def write_immutable(s3, bucket, key, data):
    payload = encode(data)

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

        existing = read_object(s3, bucket, key)

        if existing != payload:
            raise RuntimeError(
                f"SHELTER_JOURNAL_CONFLICT: {key}"
            ) from exc

    if read_object(s3, bucket, key) != payload:
        raise RuntimeError("SHELTER_JOURNAL_VERIFY_FAILED")


@contextmanager
def shelter_writer_lock(s3, bucket, run_id):
    run_time(run_id)

    record = {
        "run_id": run_id,
        "owner": uuid4().hex,
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        response = s3.put_object(
            Bucket=bucket,
            Key=LOCK_KEY,
            Body=encode(record),
            ContentType="application/json",
            IfNoneMatch="*",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")

        if code in {
            "PreconditionFailed", "412",
            "ConditionalRequestConflict", "409",
        }:
            raise RuntimeError("SHELTER_WRITER_LOCKED") from exc

        raise

    etag = response["ETag"]

    print("SHELTER_LOCK: ACQUIRED", flush=True)

    try:
        yield
    finally:
        s3.delete_object(
            Bucket=bucket,
            Key=LOCK_KEY,
            IfMatch=etag,
        )
        print("SHELTER_LOCK: RELEASED", flush=True)


def run_key(run_id, filename):
    run_time(run_id)
    return f"{PREFIX}/run_id={run_id}/{filename}"


def save_prepared(s3, bucket, run_id, prepared):
    if prepared.get("run_id") != run_id:
        raise RuntimeError("SHELTER_PREPARED_RUN_MISMATCH")

    record = {
        "run_id": run_id,
        "checksum": sha256_bytes(encode(prepared)),
        "payload": prepared,
    }

    write_immutable(
        s3, bucket,
        run_key(run_id, "prepared.json"),
        record,
    )


def load_prepared(s3, bucket, run_id):
    record = read_json(
        s3, bucket,
        run_key(run_id, "prepared.json"),
    )

    if record is None:
        return None

    payload = record["payload"]

    if record["run_id"] != run_id or payload["run_id"] != run_id:
        raise RuntimeError("SHELTER_PREPARED_RUN_MISMATCH")

    if sha256_bytes(encode(payload)) != record["checksum"]:
        raise RuntimeError("SHELTER_PREPARED_CHECKSUM_MISMATCH")

    return payload


def save_completed(s3, bucket, run_id, record):
    if record.get("run_id") != run_id:
        raise RuntimeError("SHELTER_COMPLETION_RUN_MISMATCH")

    if record.get("status") != "COMPLETED":
        raise RuntimeError("INVALID_SHELTER_COMPLETION_STATUS")

    snapshot_id = record.get("shelter_snapshot_id")

    if type(snapshot_id) is not int or snapshot_id <= 0:
        raise RuntimeError("INVALID_SHELTER_SNAPSHOT")

    write_immutable(
        s3, bucket,
        run_key(run_id, "completed.json"),
        record,
    )


def load_completed(s3, bucket, run_id):
    record = read_json(
        s3, bucket,
        run_key(run_id, "completed.json"),
    )

    if record is not None:
        if record.get("run_id") != run_id:
            raise RuntimeError("SHELTER_COMPLETION_RUN_MISMATCH")

        if record.get("status") != "COMPLETED":
            raise RuntimeError("INVALID_SHELTER_COMPLETION_STATUS")

        snapshot_id = record.get("shelter_snapshot_id")

        if type(snapshot_id) is not int or snapshot_id <= 0:
            raise RuntimeError("INVALID_SHELTER_SNAPSHOT")

    return record


def list_run_ids(s3, bucket):
    paginator = s3.get_paginator("list_objects_v2")
    runs = set()

    for page in paginator.paginate(
        Bucket=bucket,
        Prefix=f"{PREFIX}/run_id=",
        Delimiter="/",
    ):
        for item in page.get("CommonPrefixes", []):
            run_id = item["Prefix"].rstrip("/").split("run_id=")[-1]
            run_time(run_id)
            runs.add(run_id)

    return sorted(runs)
