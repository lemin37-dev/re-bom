import hashlib
import json

from botocore.exceptions import ClientError

from rebom.storage.silver.run_journal import journal_path


def image_snapshot_key(prefix, run_id):
    journal_path(run_id, ".")

    if not prefix or prefix.startswith("/"):
        raise ValueError("INVALID_S3_PREFIX")

    return f"{prefix.rstrip('/')}/images/run_id={run_id}/prepared.json"


def _encode_snapshot(run_id, table_name, expected_location, rows):
    keys = [
        (row["desertion_no"], row["image_slot"])
        for row in rows
    ]

    if len(keys) != len(set(keys)):
        raise ValueError("DUPLICATE_PREPARED_IMAGE_KEYS")

    document = {
        "run_id": run_id,
        "table_name": table_name,
        "expected_location": expected_location,
        "rows": sorted(
            rows,
            key=lambda row: (row["desertion_no"], row["image_slot"]),
        ),
    }

    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def read_image_snapshot(s3, bucket, prefix, run_id):
    key = image_snapshot_key(prefix, run_id)

    try:
        response = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")

        if code in {"NoSuchKey", "404"}:
            return None
        raise

    raw = response["Body"].read()
    document = json.loads(raw)

    if document.get("run_id") != run_id:
        raise RuntimeError("IMAGE_SNAPSHOT_RUN_ID_MISMATCH")

    expected = _encode_snapshot(
        run_id,
        document["table_name"],
        document["expected_location"],
        document["rows"],
    )

    if raw != expected:
        raise RuntimeError("IMAGE_SNAPSHOT_CONTENT_INVALID")

    return document


def save_image_snapshot(
    s3, bucket, prefix, run_id, table_name, expected_location, rows
):
    key = image_snapshot_key(prefix, run_id)
    payload = _encode_snapshot(
        run_id, table_name, expected_location, rows
    )

    digest = hashlib.sha256(payload).hexdigest()

    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=payload,
            ContentType="application/json",
            Metadata={"sha256": digest},
            IfNoneMatch="*",
        )
        return "CREATED"
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")

        if code not in {"PreconditionFailed", "412"}:
            raise

    existing = read_image_snapshot(s3, bucket, prefix, run_id)

    if existing is None:
        raise RuntimeError("IMAGE_SNAPSHOT_DISAPPEARED")

    previous = _encode_snapshot(
        existing["run_id"],
        existing["table_name"],
        existing["expected_location"],
        existing["rows"],
    )

    if previous != payload:
        raise RuntimeError("IMAGE_SNAPSHOT_CONFLICT")

    return "ALREADY_EXISTS"
