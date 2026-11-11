"""Persist immutable Silver run snapshots in S3."""

import hashlib
from pathlib import Path

from botocore.exceptions import ClientError


SNAPSHOT_FILES = (
    "inputs.json",
    "notice.parquet",
    "history.parquet",
)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_object(s3, bucket, key):
    try:
        return s3.get_object(
            Bucket=bucket,
            Key=key,
        )["Body"].read()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404"):
            return None
        raise


def save_snapshot(s3, bucket, prefix, run_id, directory):
    from rebom.storage.silver.run_journal import journal_path

    journal_path(run_id, directory)

    root = Path(directory)
    base = f"{prefix.rstrip('/')}/run_id={run_id}"

    payloads = {}

    for filename in SNAPSHOT_FILES:
        path = root / filename

        if not path.is_file():
            raise FileNotFoundError(path)

        payloads[filename] = path.read_bytes()

    for filename, payload in payloads.items():
        key = f"{base}/{filename}"
        existing = read_object(s3, bucket, key)

        if existing is not None:
            if sha256_bytes(existing) != sha256_bytes(payload):
                raise RuntimeError(
                    f"Existing snapshot conflict: {filename}"
                )
            continue

        try:
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=payload,
                IfNoneMatch="*",
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code not in ("PreconditionFailed", "412"):
                raise

            existing = read_object(s3, bucket, key)

            if existing is None or existing != payload:
                raise RuntimeError(
                    f"Concurrent snapshot conflict: {filename}"
                ) from exc

    for filename, payload in payloads.items():
        key = f"{base}/{filename}"
        stored = read_object(s3, bucket, key)

        if stored != payload:
            raise RuntimeError(
                f"Snapshot verification failed: {filename}"
            )

    return {
        "run_id": run_id,
        "files_verified": len(payloads),
        "prefix": base,
    }


def restore_snapshot(s3, bucket, prefix, run_id, directory):
    """Restore a complete immutable snapshot from S3."""

    import os
    import shutil
    import tempfile

    from rebom.storage.silver.run_journal import journal_path

    journal_path(run_id, directory)

    root = Path(directory)
    if root.exists():
        raise FileExistsError(f"Local snapshot already exists: {root}")

    base = f"{prefix.rstrip('/')}/run_id={run_id}"

    payloads = {}

    for filename in SNAPSHOT_FILES:
        key = f"{base}/{filename}"
        payloads[filename] = read_object(s3, bucket, key)

    present = [
        name for name, data in payloads.items()
        if data is not None
    ]

    if not present:
        return False

    if len(present) != len(SNAPSHOT_FILES):
        raise RuntimeError(
            f"Incomplete S3 snapshot: {present}"
        )

    root.parent.mkdir(parents=True, exist_ok=True)

    staging = Path(tempfile.mkdtemp(
        prefix=".restore-",
        dir=root.parent,
    ))

    try:
        for filename, payload in payloads.items():
            (staging / filename).write_bytes(payload)

        if root.exists():
            raise FileExistsError(root)

        os.rename(staging, root)

    finally:
        if staging.exists():
            shutil.rmtree(staging)

    return True
