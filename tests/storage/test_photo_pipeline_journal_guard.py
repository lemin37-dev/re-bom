"""사진 처리 파이프라인의 Journal 정합성 분기 테스트."""

import json
from io import BytesIO
from types import SimpleNamespace

import pytest

from rebom.storage.silver import photo_pipeline


RUN_ID = "20261105T113907-7c7069"
MANIFEST_KEY = (
    "bronze/rescue/image_runs/dt=2026-11-05/"
    f"run_id={RUN_ID}/manifest.json"
)
TABLE_NAME = "rebom_silver.silver_animal_image_b_test"
LOCATION = (
    "s3://rebom-data-559987919299/"
    "bench_tmp/b-storage-test/iceberg/silver_animal_image_b_test"
)


def make_photo():
    return {
        "desertion_no": "TEST-001",
        "image_slot": 1,
        "original_s3_key": "bronze/rescue/images/TEST-001/popfile1.jpg",
        "serving_s3_key": "images/TEST-001/1.webp",
        "thumb_s3_key": "images/TEST-001/1_thumb.webp",
        "sha256": "a" * 64,
    }


class FakeTable:
    def __init__(self, rows):
        self.rows = rows

    def location(self):
        return LOCATION

    def scan(self):
        return SimpleNamespace(
            to_arrow=lambda: SimpleNamespace(
                to_pylist=lambda: self.rows
            )
        )


class FakeCatalog:
    def __init__(self, rows):
        self.table = FakeTable(rows)

    def load_table(self, name):
        assert name == TABLE_NAME
        return self.table


class FakeS3:
    def get_object(self, Bucket, Key):
        assert Key == MANIFEST_KEY
        return {
            "Body": BytesIO(
                json.dumps(
                    {"run_id": RUN_ID, "status": "ok"}
                ).encode("utf-8")
            )
        }


@pytest.fixture
def photo_with_journal(monkeypatch):
    photo = make_photo()

    monkeypatch.setattr(
        photo_pipeline,
        "load_photo_manifest",
        lambda path: {
            "run_id": RUN_ID,
            "status": "ok",
            "rows": [photo],
        },
    )

    monkeypatch.setattr(
        photo_pipeline,
        "decide_photo_manifest",
        lambda source: {"decision": "APPLY"},
    )

    monkeypatch.setattr(
        photo_pipeline,
        "load_photo_journal_s3",
        lambda s3, bucket, key: {
            "run_id": RUN_ID,
            "manifest_key": MANIFEST_KEY,
            "manifest_status": "ok",
            "processing_status": "APPLIED",
            "applied_count": 1,
        },
    )

    return photo


def execute(rows):
    return photo_pipeline.process_photo_manifest(
        s3=FakeS3(),
        catalog=FakeCatalog(rows),
        bucket="rebom-data-559987919299",
        manifest_key=MANIFEST_KEY,
        table_name=TABLE_NAME,
        expected_location=LOCATION,
        journal_prefix="bench_tmp/b-storage-test/journals/photo_runs",
        apply=False,
    )


def test_existing_journal_and_iceberg_match(photo_with_journal):
    result = execute([photo_with_journal.copy()])

    assert result["result"] == "ALREADY_PROCESSED"
    assert result["iceberg_verified"] is True


def test_existing_journal_but_iceberg_row_missing(photo_with_journal):
    with pytest.raises(
        RuntimeError,
        match="Journal 존재하지만 Iceberg 행 누락",
    ):
        execute([])


def test_existing_journal_but_sha256_mismatch(photo_with_journal):
    wrong = photo_with_journal.copy()
    wrong["sha256"] = "b" * 64

    with pytest.raises(
        RuntimeError,
        match="Journal과 Iceberg 데이터 불일치",
    ):
        execute([wrong])
