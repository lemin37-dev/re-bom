import io
import json

import pytest

from scripts.storage.rebom_auto_process_photo_manifests import (
    filter_manifests_by_bronze_run,
)


class FakeS3:
    def __init__(self, manifests):
        self.manifests = manifests

    def get_object(self, Bucket, Key):
        data = json.dumps(self.manifests[Key]).encode("utf-8")
        return {"Body": io.BytesIO(data)}


def test_filter_matching_bronze_run():
    s3 = FakeS3({
        "a": {"bronze_run_id": "run-A"},
        "b": {"bronze_run_id": "run-B"},
        "c": {"bronze_run_id": "run-A"},
    })

    result = filter_manifests_by_bronze_run(
        s3, "test-bucket", ["a", "b", "c"], "run-A"
    )

    assert result == ["a", "c"]


def test_filter_excludes_other_bronze_run():
    s3 = FakeS3({
        "a": {"bronze_run_id": "run-A"},
        "b": {"bronze_run_id": "run-B"},
    })

    result = filter_manifests_by_bronze_run(
        s3, "test-bucket", ["a", "b"], "run-B"
    )

    assert result == ["b"]


def test_filter_rejects_missing_bronze_run():
    s3 = FakeS3({
        "a": {"bronze_run_id": "run-A"},
    })

    with pytest.raises(
        RuntimeError, match="PHOTO_MANIFEST_NOT_FOUND"
    ):
        filter_manifests_by_bronze_run(
            s3, "test-bucket", ["a"], "run-C"
        )

def test_bronze_filter_allows_missing_when_deferred():
    from unittest.mock import MagicMock

    from scripts.storage.rebom_auto_process_photo_manifests import (
        filter_manifests_by_bronze_run,
    )

    result = filter_manifests_by_bronze_run(
        s3=MagicMock(),
        bucket="rebom-data-559987919299",
        keys=[],
        bronze_run_id="20261118T030011-6f2d1f",
        allow_missing=True,
    )

    assert result == []
