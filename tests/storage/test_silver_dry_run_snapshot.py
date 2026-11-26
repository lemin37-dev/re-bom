from io import BytesIO
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from rebom.storage.silver.s3_run_snapshot import (
    SNAPSHOT_FILES,
    restore_snapshot,
)


RUN_ID = "20261117T030011-8d0526"
BUCKET = "test-bucket"
PREFIX = "test/silver"


class FakeS3:
    def __init__(self, objects=None):
        self.objects = objects or {}
        self.write_calls = 0

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError(
                {
                    "Error": {
                        "Code": "NoSuchKey",
                        "Message": "Not found",
                    }
                },
                "GetObject",
            )
        return {"Body": BytesIO(self.objects[Key])}

    def put_object(self, **kwargs):
        self.write_calls += 1
        raise AssertionError("DRY_RUN must not write S3 objects")


def snapshot_objects():
    base = f"{PREFIX}/run_id={RUN_ID}"
    return {
        f"{base}/{name}": f"test-{name}".encode()
        for name in SNAPSHOT_FILES
    }


def test_restore_existing_snapshot(tmp_path):
    s3 = FakeS3(snapshot_objects())
    target = tmp_path / "prepared" / RUN_ID

    result = restore_snapshot(s3, BUCKET, PREFIX, RUN_ID, target)

    assert result is True
    assert all((target / name).is_file() for name in SNAPSHOT_FILES)
    assert s3.write_calls == 0


def test_restore_missing_snapshot_returns_false(tmp_path):
    s3 = FakeS3()
    target = tmp_path / "prepared" / RUN_ID

    result = restore_snapshot(s3, BUCKET, PREFIX, RUN_ID, target)

    assert result is False
    assert not target.exists()
    assert s3.write_calls == 0


def test_restore_incomplete_snapshot_fails(tmp_path):
    objects = snapshot_objects()
    objects.pop(f"{PREFIX}/run_id={RUN_ID}/history.parquet")
    s3 = FakeS3(objects)
    target = tmp_path / "prepared" / RUN_ID

    with pytest.raises(RuntimeError, match="Incomplete S3 snapshot"):
        restore_snapshot(s3, BUCKET, PREFIX, RUN_ID, target)

    assert not target.exists()
    assert s3.write_calls == 0


def test_restore_does_not_overwrite_local_snapshot(tmp_path):
    s3 = FakeS3(snapshot_objects())
    target = tmp_path / "prepared" / RUN_ID
    target.mkdir(parents=True)
    existing = target / "inputs.json"
    existing.write_bytes(b"existing-data")

    with pytest.raises(FileExistsError):
        restore_snapshot(s3, BUCKET, PREFIX, RUN_ID, target)

    assert existing.read_bytes() == b"existing-data"
    assert s3.write_calls == 0
