"""?? Manifest ?? Journal S3 ?? ??."""

import io

import pytest
from botocore.exceptions import ClientError

from rebom.storage.silver.photo_pending_journal import (
    TEST_JOURNAL_PREFIX,
    list_pending_manifests,
    mark_pending_resolved,
    pending_key,
    save_pending_manifest,
)


BUCKET = "rebom-data-559987919299"
BRONZE_ID = "20261118T030011-6f2d1f"
PHOTO_ID = "20261118T030135-82547e"
PREFIX = TEST_JOURNAL_PREFIX


class MemoryS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        value = self.objects.get((Bucket, Key))

        if value is None:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey"}},
                "GetObject",
            )

        return {"Body": io.BytesIO(value)}

    def put_object(
        self, Bucket, Key, Body, ContentType, IfNoneMatch
    ):
        assert ContentType == "application/json"
        assert IfNoneMatch == "*"

        identifier = (Bucket, Key)

        if identifier in self.objects:
            raise ClientError(
                {"Error": {"Code": "PreconditionFailed"}},
                "PutObject",
            )

        self.objects[identifier] = Body

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        outer = self

        class Paginator:
            def paginate(self, Bucket, Prefix):
                keys = sorted(
                    key
                    for bucket, key in outer.objects
                    if bucket == Bucket and key.startswith(Prefix)
                )
                yield {
                    "Contents": [{"Key": key} for key in keys]
                }

        return Paginator()


@pytest.mark.parametrize(
    "prefix,run_id",
    [
        ("silver/rebom/journals", BRONZE_ID),
        (PREFIX, "invalid-run-id"),
    ],
)
def test_pending_rejects_invalid_target(prefix, run_id):
    with pytest.raises(ValueError):
        pending_key(prefix, run_id)


def test_pending_create_and_duplicate_prevention():
    s3 = MemoryS3()

    result = save_pending_manifest(
        s3, BUCKET, PREFIX, BRONZE_ID
    )
    assert result == "CREATED"

    key = (BUCKET, pending_key(PREFIX, BRONZE_ID))
    original = s3.objects[key]

    repeated = save_pending_manifest(
        s3, BUCKET, PREFIX, BRONZE_ID
    )

    assert repeated == "ALREADY_EXISTS"
    assert s3.objects[key] == original
    assert list_pending_manifests(s3, BUCKET, PREFIX) == [
        BRONZE_ID
    ]


def test_pending_resolution_and_idempotency():
    s3 = MemoryS3()

    save_pending_manifest(s3, BUCKET, PREFIX, BRONZE_ID)

    result = mark_pending_resolved(
        s3, BUCKET, PREFIX, BRONZE_ID, [PHOTO_ID]
    )
    assert result == "CREATED"

    assert list_pending_manifests(s3, BUCKET, PREFIX) == []

    repeated = mark_pending_resolved(
        s3, BUCKET, PREFIX, BRONZE_ID, [PHOTO_ID]
    )
    assert repeated == "ALREADY_EXISTS"

    assert save_pending_manifest(
        s3, BUCKET, PREFIX, BRONZE_ID
    ) == "ALREADY_RESOLVED"


def test_pending_resolution_conflict_blocked():
    s3 = MemoryS3()
    save_pending_manifest(s3, BUCKET, PREFIX, BRONZE_ID)

    mark_pending_resolved(
        s3, BUCKET, PREFIX, BRONZE_ID, [PHOTO_ID]
    )

    with pytest.raises(
        RuntimeError,
        match="PHOTO_PENDING_JOURNAL_CONFLICT",
    ):
        mark_pending_resolved(
            s3,
            BUCKET,
            PREFIX,
            BRONZE_ID,
            ["20261117T105112-42b2b8"],
        )


def test_resolution_requires_pending_record():
    s3 = MemoryS3()

    assert mark_pending_resolved(
        s3, BUCKET, PREFIX, BRONZE_ID, [PHOTO_ID]
    ) == "NOT_PENDING"


def test_resolution_rejects_invalid_photo_run_id():
    s3 = MemoryS3()
    save_pending_manifest(s3, BUCKET, PREFIX, BRONZE_ID)

    with pytest.raises(
        ValueError,
        match="INVALID_PHOTO_RUN_ID",
    ):
        mark_pending_resolved(
            s3, BUCKET, PREFIX, BRONZE_ID, ["invalid"]
        )
