"""사진 이력 저장 후 중단 및 재실행 복구 테스트."""

import json
from io import BytesIO

import pyarrow as pa
import pytest
from pyiceberg.catalog import load_catalog

from rebom.storage.silver import photo_pipeline
from rebom.storage.silver import photo_version_history


def make_version(version, status, sha):
    return {
        "desertion_no": "TEST-001",
        "image_slot": 1,
        "version": version,
        "replaces": version - 1 if version > 1 else None,
        "first_seen_at": f"2026-11-{13 + version:02d}T01:00:00Z",
        "source_url": f"https://example.com/v{version}.jpg",
        "original_s3_key": (
            f"bronze/rescue/images/TEST-001/popfile1_v{version:03d}.jpg"
            if sha else None
        ),
        "serving_s3_key": (
            f"images/TEST-001/1_v{version:03d}.webp"
            if sha else None
        ),
        "thumb_s3_key": (
            f"images/TEST-001/1_v{version:03d}_thumb.webp"
            if sha else None
        ),
        "sha256": sha,
        "status": status,
        "failure_reason": "replaced_not_found" if status == "failed" else None,
        "run_id": f"RUN-{version:03d}",
    }


def test_history_saved_then_current_update_fails_and_recovers(
    tmp_path, monkeypatch
):
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()

    catalog = load_catalog(
        "photo_pipeline_recovery",
        type="sql",
        uri=f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}",
        warehouse=str(warehouse),
    )
    catalog.create_namespace("rebom_test")

    history_schema = pa.schema([
        pa.field("desertion_no", pa.string(), nullable=False),
        pa.field("image_slot", pa.int32(), nullable=False),
        pa.field("version", pa.int32(), nullable=False),
        pa.field("replaces", pa.int32()),
        pa.field("first_seen_at", pa.string()),
        pa.field("source_url", pa.string()),
        pa.field("original_s3_key", pa.string()),
        pa.field("serving_s3_key", pa.string()),
        pa.field("thumb_s3_key", pa.string()),
        pa.field("sha256", pa.string()),
        pa.field("status", pa.string()),
        pa.field("failure_reason", pa.string()),
        pa.field("run_id", pa.string()),
    ])

    image_schema = pa.schema([
        pa.field("desertion_no", pa.string(), nullable=False),
        pa.field("image_slot", pa.int32(), nullable=False),
        pa.field("original_s3_key", pa.string()),
        pa.field("serving_s3_key", pa.string()),
        pa.field("thumb_s3_key", pa.string()),
        pa.field("sha256", pa.string()),
    ])

    history_name = "rebom_test.photo_history"
    image_name = "rebom_test.photo_current"

    history_table = catalog.create_table(
        history_name, schema=history_schema
    )
    image_table = catalog.create_table(
        image_name, schema=image_schema
    )

    v1 = make_version(1, "stored", "a" * 64)
    v2 = make_version(2, "failed", None)
    v3 = make_version(3, "stored", "c" * 64)

    photo_version_history.apply_photo_version_history(
        catalog,
        history_name,
        [v1, v2],
        history_table.location(),
        apply=True,
    )

    image_fields = image_schema.names
    image_table.append(
        pa.Table.from_pylist(
            [{field: v1[field] for field in image_fields}],
            schema=image_schema,
        )
    )

    run_id = "RUN-003"
    manifest_key = (
        "bronze/rescue/image_runs/dt=2026-11-16/"
        f"run_id={run_id}/manifest.json"
    )

    manifest = {
        "run_id": run_id,
        "status": "ok",
        "rows": [v3],
        "failed_rows": [],
    }

    class FakeS3:
        def get_object(self, Bucket, Key):
            assert Key == manifest_key
            return {
                "Body": BytesIO(
                    json.dumps({
                        "run_id": run_id,
                        "status": "ok",
                        "files": [v3],
                        "failed": [],
                    }).encode("utf-8")
                )
            }

        def head_object(self, Bucket, Key):
            assert Key == v3["original_s3_key"]
            return {"Metadata": {"sha256": v3["sha256"]}}

    monkeypatch.setattr(
        photo_pipeline,
        "load_photo_manifest",
        lambda path: manifest,
    )

    journals = {}

    monkeypatch.setattr(
        photo_pipeline,
        "load_photo_journal_s3",
        lambda s3, bucket, key: journals.get(key),
    )

    def save_journal(s3, bucket, key, journal):
        journals[key] = journal
        return "CREATED"

    monkeypatch.setattr(
        photo_pipeline,
        "save_photo_journal_s3",
        save_journal,
    )

    original_apply = photo_version_history.apply_photo_current_updates
    failed_once = {"value": False}

    def interrupt_current(*args, **kwargs):
        if kwargs.get("apply") and not failed_once["value"]:
            failed_once["value"] = True
            raise RuntimeError("SIMULATED_CURRENT_WRITE_FAILURE")
        return original_apply(*args, **kwargs)

    monkeypatch.setattr(
        photo_version_history,
        "apply_photo_current_updates",
        interrupt_current,
    )

    def execute():
        return photo_pipeline.process_photo_manifest(
            s3=FakeS3(),
            catalog=catalog,
            bucket="test-bucket",
            manifest_key=manifest_key,
            table_name=image_name,
            expected_location=image_table.location(),
            journal_prefix="test/journals",
            history_table_name=history_name,
            expected_history_location=history_table.location(),
            apply=True,
        )

    with pytest.raises(
        RuntimeError, match="SIMULATED_CURRENT_WRITE_FAILURE"
    ):
        execute()

    history_after_failure = (
        catalog.load_table(history_name).scan().to_arrow().to_pylist()
    )
    image_after_failure = (
        catalog.load_table(image_name).scan().to_arrow().to_pylist()
    )

    assert len(history_after_failure) == 3
    assert image_after_failure[0]["sha256"] == "a" * 64
    assert journals == {}

    recovered = execute()

    assert recovered["result"] == "APPLIED_AND_VERIFIED"
    assert recovered["history_inserted"] == 0
    assert recovered["current_changed"] == 1

    image_after_recovery = (
        catalog.load_table(image_name).scan().to_arrow().to_pylist()
    )
    assert image_after_recovery[0]["sha256"] == "c" * 64

    repeated = execute()
    assert repeated["result"] == "ALREADY_PROCESSED"

    final_history = (
        catalog.load_table(history_name).scan().to_arrow().to_pylist()
    )
    assert len(final_history) == 3
