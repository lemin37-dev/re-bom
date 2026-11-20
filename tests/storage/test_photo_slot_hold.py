import hashlib
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pyarrow as pa
import pytest

from rebom.storage.silver import photo_pipeline
from rebom.storage.silver.photo_version_history import build_photo_version_history_rows


@pytest.fixture
def photo_run():
    photos = [
        {
            "desertion_no": f"TEST-{i:03d}",
            "image_slot": 1,
            "version": 2 if i == 0 else 1,
            "replaces": 1 if i == 0 else None,
            "original_s3_key": f"original/{i}.jpg",
            "serving_s3_key": f"serving/{i}.webp",
            "thumb_s3_key": f"thumb/{i}.webp",
            "sha256": "a" * 64,
        }
        for i in range(480)
    ]
    manifest = {"run_id": "TEST-RUN", "status": "ok", "rows": photos}
    history_rows = build_photo_version_history_rows(manifest)
    history_schema = pa.schema([
        (name, pa.int32() if name in {"image_slot", "version", "replaces"} else pa.string())
        for name in history_rows[0]
    ])
    current_schema = pa.schema([
        history_schema.field(name)
        for name in ("desertion_no", "image_slot", "source_url", *photo_pipeline.PHOTO_FIELDS)
    ])

    def mock_table(name, schema):
        table = Mock()
        rows = []
        table.location.return_value = f"s3://test/{name}"
        table.schema.return_value.as_arrow.return_value = schema
        table.scan.return_value.to_arrow.return_value.to_pylist.side_effect = (
            lambda: list(rows)
        )

        def insert(arrow, **kwargs):
            new_rows = arrow.to_pylist()
            rows.extend(new_rows)
            return SimpleNamespace(rows_inserted=len(new_rows), rows_updated=0)

        table.upsert.side_effect = insert
        table.append.side_effect = insert
        return table, rows

    history, saved_history = mock_table("history", history_schema)
    current, saved_current = mock_table("current", current_schema)
    catalog = Mock()
    catalog.load_table.side_effect = {"history": history, "current": current}.__getitem__
    s3 = Mock()
    s3.head_object.return_value = {"Metadata": {"sha256": "a" * 64}}

    def execute(*, apply=False, **kwargs):
        return photo_pipeline._process_photo_manifest_versioned(
            s3=s3, catalog=catalog, bucket="test", manifest=manifest,
            manifest_key="manifest.json", table_name="current",
            expected_location=current.location(), history_table_name="history",
            expected_history_location=history.location(), journal_key="journal.json",
            decision={"decision": "APPLY", "failed_count": 0, "needs_review": False},
            apply=apply,
            **kwargs,
        )

    return SimpleNamespace(
        execute=execute, manifest=manifest, s3=s3, history=history, current=current,
        saved_history=saved_history, saved_current=saved_current, catalog=catalog,
    )


def assert_no_writes(run):
    run.history.upsert.assert_not_called()
    run.history.append.assert_not_called()
    run.history.overwrite.assert_not_called()
    run.current.overwrite.assert_not_called()
    run.current.append.assert_not_called()
    run.s3.put_object.assert_not_called()


def test_dry_run_plans_479_of_480_slots_without_writes(photo_run):
    result = photo_run.execute()

    assert result["result"] == "DRY_RUN_PASS"
    assert result["history_insert_count"] == result["changed_rows"] == 479
    assert result["held_slots"] == [{
        "desertion_no": "TEST-000", "image_slot": 1, "version": 2,
        "replaces": 1, "reason": "MISSING_PREVIOUS_VERSION",
    }]
    assert photo_run.s3.head_object.call_count == 480
    assert_no_writes(photo_run)


def test_held_slot_excluded_from_both_tables_and_rerun_skips_writes(photo_run):
    first = photo_run.execute(apply=True)
    assert first["result"] == "APPLIED_WITH_HELD_SLOTS"
    assert first["history_inserted"] == first["current_changed"] == 479
    expected = {f"TEST-{i:03d}" for i in range(1, 480)}
    for rows in (photo_run.saved_history, photo_run.saved_current):
        assert len(rows) == 479
        assert {row["desertion_no"] for row in rows} == expected
    photo_run.s3.put_object.assert_not_called()

    photo_run.history.upsert.reset_mock()
    photo_run.current.append.reset_mock()
    second = photo_run.execute(apply=True)
    assert second["held_slots"] == first["held_slots"]
    assert second["history_inserted"] == second["current_changed"] == 0
    assert_no_writes(photo_run)


@pytest.mark.parametrize("error", ["checksum", "replaces", "conflict", "current"])
def test_fatal_slot_error_stops_before_any_write(photo_run, error):
    last = photo_run.manifest["rows"][-1]
    if error == "checksum":
        last["sha256"] = "b" * 64
    elif error == "replaces":
        last["replaces"] = 2
    elif error == "conflict":
        last_history = build_photo_version_history_rows(photo_run.manifest)[-1]
        photo_run.saved_history.append(dict(last_history, sha256="b" * 64))
    else:
        photo_run.saved_current.append({
            "desertion_no": last["desertion_no"], "image_slot": 1, "sha256": "b" * 64,
        })

    with pytest.raises((ValueError, RuntimeError)):
        photo_run.execute(apply=True)
    assert_no_writes(photo_run)


def test_storage_failure_stops_without_journal(photo_run):
    photo_run.history.upsert.side_effect = RuntimeError("SIMULATED_WRITE_FAILURE")
    with pytest.raises(RuntimeError, match="SIMULATED_WRITE_FAILURE"):
        photo_run.execute(apply=True)
    photo_run.current.append.assert_not_called()
    photo_run.s3.put_object.assert_not_called()


def test_journal_write_failure_propagates_after_storage(photo_run):
    photo_run.manifest["rows"][0].update(version=1, replaces=None)
    photo_run.s3.put_object.side_effect = RuntimeError("SIMULATED_JOURNAL_FAILURE")

    with pytest.raises(RuntimeError, match="SIMULATED_JOURNAL_FAILURE"):
        photo_run.execute(apply=True)

    assert len(photo_run.saved_history) == len(photo_run.saved_current) == 480
    photo_run.s3.put_object.assert_called_once()


@pytest.fixture
def historical_v1(photo_run):
    photo_run.manifest["generated_at"] = "2026-11-20T10:00:00+09:00"
    key = "bronze/rescue/image_runs/dt=2026-11-17/run_id=old/manifest.json"
    original = "bronze/rescue/images/TEST-000/popfile1.jpg"
    body = b"verified v1"
    file = {
        "desertionNo": "TEST-000", "slot": 1, "version": 1, "replaces": None,
        "original": original, "ext": "jpg", "sha256": hashlib.sha256(body).hexdigest(),
        "serving": {"image": "images/TEST-000/1.webp", "thumb": "images/TEST-000/1_thumb.webp"},
    }
    source = {
        "run_id": "old", "bronze_run_id": "old-bronze", "status": "ok",
        "updated_at": "2026-11-17T10:00:00+09:00",
        "files": [file], "failed": [],
    }
    objects = {original: body}

    def get_object(*, Bucket, Key):
        return {"Body": io.BytesIO(json.dumps(source).encode() if Key == key else objects[Key])}

    photo_run.s3.get_object.side_effect = get_object
    return SimpleNamespace(key=key, original=original, objects=objects, source=source)


def test_dry_run_uses_verified_v1_only_for_comparison(photo_run, historical_v1):
    result = photo_run.execute(historical_manifest_keys=[historical_v1.key])

    assert result["result"] == "DRY_RUN_PASS"
    assert result["held_slots"] == []
    assert result["history_insert_count"] == result["changed_rows"] == 480
    assert photo_run.s3.get_object.call_count == 2
    assert photo_run.saved_history == photo_run.saved_current == []
    assert_no_writes(photo_run)


def test_historical_v1_hash_mismatch_blocks_plan(photo_run, historical_v1):
    historical_v1.objects[historical_v1.original] = b"changed bytes"
    with pytest.raises(RuntimeError, match="PHOTO_BASELINE_SHA256_MISMATCH"):
        photo_run.execute(historical_manifest_keys=[historical_v1.key])
    assert_no_writes(photo_run)


@pytest.mark.parametrize("mode", ["missing", "failed", "in_progress", "future"])
def test_unverified_v1_leaves_slot_held(photo_run, historical_v1, mode):
    source = historical_v1.source
    if mode in {"missing", "failed"}:
        source["files"] = []
    if mode == "failed":
        source["failed"] = [{"desertionNo": "TEST-000", "slot": 1, "version": 1,
                             "reason": "not_found", "source_url": "https://example.test/photo"}]
    if mode == "in_progress":
        source["status"] = "in_progress"
    if mode == "future":
        source["updated_at"] = "2026-11-21T10:00:00+09:00"

    result = photo_run.execute(historical_manifest_keys=[historical_v1.key])
    assert result["history_insert_count"] == result["changed_rows"] == 479
    assert result["held_slots"][0]["reason"] == "MISSING_PREVIOUS_VERSION"
    assert photo_run.s3.get_object.call_count == 1
    assert_no_writes(photo_run)


@pytest.mark.parametrize("field,value", [
    ("sha256", "b" * 64), ("original_s3_key", "different.jpg"), ("replaces", 0),
])
def test_historical_v1_conflict_with_silver_history_blocks_plan(
    photo_run, historical_v1, field, value
):
    from rebom.storage.silver.load_photo_manifest import parse_photo_manifest

    baseline, = build_photo_version_history_rows(parse_photo_manifest(historical_v1.source))
    photo_run.saved_history.append(dict(baseline, **{field: value}))
    with pytest.raises(ValueError, match="PHOTO_BASELINE_HISTORY_CONFLICT"):
        photo_run.execute(historical_manifest_keys=[historical_v1.key])
    assert_no_writes(photo_run)


def test_historical_v1_conflict_with_silver_current_blocks_plan(photo_run, historical_v1):
    photo_run.saved_current.append({
        "desertion_no": "TEST-000", "image_slot": 1, "sha256": "b" * 64,
    })
    with pytest.raises(ValueError, match="PHOTO_BASELINE_CURRENT_CONFLICT"):
        photo_run.execute(historical_manifest_keys=[historical_v1.key])
    assert_no_writes(photo_run)


def test_historical_manifests_are_skipped_when_previous_version_exists(photo_run, historical_v1):
    from rebom.storage.silver.load_photo_manifest import parse_photo_manifest

    baseline, = build_photo_version_history_rows(parse_photo_manifest(historical_v1.source))
    photo_run.saved_history.append(baseline)
    photo_run.saved_current.append({name: baseline[name] for name in (
        "desertion_no", "image_slot", "source_url", *photo_pipeline.PHOTO_FIELDS,
    )})
    result = photo_run.execute(historical_manifest_keys=[historical_v1.key])
    assert result["held_slots"] == []
    photo_run.s3.get_object.assert_not_called()
    assert_no_writes(photo_run)


def test_apply_rejects_historical_keys_and_comparison_baseline(photo_run, historical_v1):
    from rebom.storage.silver.photo_version_history import apply_photo_current_updates

    with pytest.raises(ValueError, match="PHOTO_BASELINE_DRY_RUN_ONLY"):
        photo_run.execute(apply=True, historical_manifest_keys=[historical_v1.key])
    catalog = Mock()
    with pytest.raises(ValueError, match="PHOTO_BASELINE_DRY_RUN_ONLY"):
        apply_photo_current_updates(
            catalog, "current", [], "s3://test/current", apply=True, dry_run_baseline=Mock()
        )
    catalog.load_table.assert_not_called()
    photo_run.s3.get_object.assert_not_called()
    assert_no_writes(photo_run)


def test_process_photo_manifest_threads_historical_keys(photo_run, historical_v1, monkeypatch):
    latest_key = "bronze/rescue/image_runs/dt=2026-11-20/run_id=new/manifest.json"
    latest = dict(
        historical_v1.source,
        run_id="new", bronze_run_id="new-bronze",
        generated_at="2026-11-20T10:00:00+09:00",
        updated_at="2026-11-20T10:01:00+09:00",
        files=[dict(
            historical_v1.source["files"][0],
            version=2, replaces=1, sha256="a" * 64,
            original="bronze/rescue/images/TEST-000/popfile1_v002.jpg",
            serving={
                "image": "images/TEST-000/1_v002.webp",
                "thumb": "images/TEST-000/1_v002_thumb.webp",
            },
            first_seen_at="2026-11-20T10:00:00+09:00",
            source_url="https://example.test/v2",
        )],
    )
    historical_v1.objects[latest_key] = json.dumps(latest).encode()
    monkeypatch.setattr(photo_pipeline, "load_photo_journal_s3", Mock(return_value=None))

    result = photo_pipeline.process_photo_manifest(
        s3=photo_run.s3, catalog=photo_run.catalog, bucket="test",
        manifest_key=latest_key, table_name="current", expected_location="s3://test/current",
        journal_prefix="journal", history_table_name="history",
        expected_history_location="s3://test/history",
        historical_manifest_keys=[historical_v1.key, latest_key],
    )

    assert result["result"] == "DRY_RUN_PASS"
    assert result["held_slots"] == []
    assert result["history_insert_count"] == result["changed_rows"] == 1
    assert photo_run.s3.get_object.call_count == 3
    assert_no_writes(photo_run)
