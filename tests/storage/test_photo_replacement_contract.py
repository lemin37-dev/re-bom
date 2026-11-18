import pytest

from rebom.storage.silver.image_incremental import prepare_safe_image_inserts


def test_photo_url_change_current_behavior():
    existing = [
        {
            "desertion_no": "TEST-001",
            "image_slot": 1,
            "source_url": "http://example.com/photo_v1.jpg",
        }
    ]

    incoming = [
        {
            "desertionNo": "TEST-001",
            "popfile1": "http://example.com/photo_v2.jpg",
        }
    ]

    assert prepare_safe_image_inserts(incoming, existing) == []




import json

from rebom.storage.silver.load_photo_manifest import load_photo_manifest


def test_photo_version_2_manifest_contract(tmp_path):
    manifest = {
        "run_id": "TEST-PHOTO-RUN-002",
        "bronze_run_id": "TEST-BRONZE-RUN-001",
        "status": "ok",
        "failed": [],
        "files": [
            {
                "desertionNo": "TEST-001",
                "slot": 1,
                "original": "bronze/rescue/images/TEST-001/popfile1_v002.jpg",
                "ext": "jpg",
                "sha256": "a" * 64,
                "source_url": "http://example.com/photo_v2.jpg",
                "version": 2,
                "replaces": 1,
                "first_seen_at": "2026-11-16T01:00:00Z",
                "serving": {
                    "image": "images/TEST-001/1_v002.webp",
                    "thumb": "images/TEST-001/1_v002_thumb.webp",
                },
            }
        ],
    }

    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    result = load_photo_manifest(path)

    assert result["rows"][0]["version"] == 2
    assert result["rows"][0]["replaces"] == 1

def test_photo_v2_hash_conflict_current_behavior(monkeypatch):
    import json
    from io import BytesIO
    from types import SimpleNamespace

    from rebom.storage.silver import photo_pipeline

    run_id = "TEST-PHOTO-V2"
    manifest_key = (
        "bronze/rescue/image_runs/dt=2026-11-16/"
        f"run_id={run_id}/manifest.json"
    )
    location = "s3://test-bucket/test-image-table"

    existing = {
        "desertion_no": "TEST-001",
        "image_slot": 1,
        "source_url": "http://example.com/photo_v1.jpg",
        "original_s3_key": "bronze/rescue/images/TEST-001/popfile1.jpg",
        "serving_s3_key": "images/TEST-001/1.webp",
        "thumb_s3_key": "images/TEST-001/1_thumb.webp",
        "sha256": "a" * 64,
    }

    incoming = {
        "desertion_no": "TEST-001",
        "image_slot": 1,
        "source_url": "http://example.com/photo_v2.jpg",
        "original_s3_key": "bronze/rescue/images/TEST-001/popfile1_v002.jpg",
        "serving_s3_key": "images/TEST-001/1_v002.webp",
        "thumb_s3_key": "images/TEST-001/1_v002_thumb.webp",
        "sha256": "b" * 64,
        "version": 2,
        "replaces": 1,
        "first_seen_at": "2026-11-16T01:00:00Z",
    }

    class FakeS3:
        def get_object(self, Bucket, Key):
            assert Key == manifest_key
            return {
                "Body": BytesIO(
                    json.dumps({"run_id": run_id, "status": "ok"}).encode()
                )
            }

        def head_object(self, Bucket, Key):
            assert Key == incoming["original_s3_key"]
            return {"Metadata": {"sha256": "b" * 64}}

    class FakeTable:
        def location(self):
            return location

        def schema(self):
            return SimpleNamespace(
                as_arrow=lambda: SimpleNamespace(
                    names=list(existing.keys())
                )
            )

        def scan(self):
            return SimpleNamespace(
                to_arrow=lambda: SimpleNamespace(
                    to_pylist=lambda: [existing.copy()]
                )
            )

    class FakeCatalog:
        def load_table(self, name):
            assert name == "rebom_silver.test_image"
            return FakeTable()

    monkeypatch.setattr(
        photo_pipeline,
        "load_photo_manifest",
        lambda path: {
            "run_id": run_id,
            "status": "ok",
            "rows": [incoming],
        },
    )
    monkeypatch.setattr(
        photo_pipeline,
        "decide_photo_manifest",
        lambda source: {
            "decision": "APPLY",
            "failed_count": 0,
        },
    )
    monkeypatch.setattr(
        photo_pipeline,
        "load_photo_journal_s3",
        lambda s3, bucket, key: None,
    )

    with pytest.raises(RuntimeError, match="기존 사진 해시 충돌"):
        photo_pipeline.process_photo_manifest(
            s3=FakeS3(),
            catalog=FakeCatalog(),
            bucket="test-bucket",
            manifest_key=manifest_key,
            table_name="rebom_silver.test_image",
            expected_location=location,
            journal_prefix="test/journals",
            apply=False,
        )


def test_photo_replacement_404_history_contract(tmp_path):
    manifest = {
        "run_id": "TEST-PHOTO-RUN-404",
        "bronze_run_id": "TEST-BRONZE-RUN-001",
        "status": "ok",
        "files": [],
        "failed": [
            {
                "desertionNo": "TEST-001",
                "slot": 1,
                "source_url": "http://example.com/photo_v2.jpg",
                "version": 2,
                "replaces": 1,
                "first_seen_at": "2026-11-16T01:00:00Z",
                "reason": "replaced_not_found",
            }
        ],
    }

    path = tmp_path / "manifest_404.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    result = load_photo_manifest(path)

    assert result["rows"] == []
    assert len(result["failed_rows"]) == 1

    failed = result["failed_rows"][0]

    assert failed["desertion_no"] == "TEST-001"
    assert failed["image_slot"] == 1
    assert failed["version"] == 2
    assert failed["replaces"] == 1
    assert failed["reason"] == "replaced_not_found"
    assert failed["first_seen_at"] == "2026-11-16T01:00:00Z"


def test_photo_version_history_v1_failed_v2_stored_v3():
    from rebom.storage.silver.photo_version_history import (
        build_photo_version_history_rows,
    )

    manifest = {
        "run_id": "TEST-RUN-003",
        "rows": [
            {
                "desertion_no": "TEST-001",
                "image_slot": 1,
                "version": 1,
                "replaces": None,
                "source_url": "http://example.com/v1.jpg",
                "first_seen_at": "2026-11-14T01:00:00Z",
                "original_s3_key": "bronze/rescue/images/TEST-001/popfile1.jpg",
                "serving_s3_key": "images/TEST-001/1.webp",
                "thumb_s3_key": "images/TEST-001/1_thumb.webp",
                "sha256": "a" * 64,
            },
            {
                "desertion_no": "TEST-001",
                "image_slot": 1,
                "version": 3,
                "replaces": 2,
                "source_url": "http://example.com/v3.jpg",
                "first_seen_at": "2026-11-16T01:00:00Z",
                "original_s3_key": "bronze/rescue/images/TEST-001/popfile1_v003.jpg",
                "serving_s3_key": "images/TEST-001/1_v003.webp",
                "thumb_s3_key": "images/TEST-001/1_v003_thumb.webp",
                "sha256": "c" * 64,
            },
        ],
        "failed_rows": [
            {
                "desertion_no": "TEST-001",
                "image_slot": 1,
                "version": 2,
                "replaces": 1,
                "source_url": "http://example.com/v2.jpg",
                "first_seen_at": "2026-11-15T01:00:00Z",
                "reason": "replaced_not_found",
            }
        ],
    }

    result = build_photo_version_history_rows(manifest)

    assert len(result) == 3
    assert [row["version"] for row in result] == [1, 2, 3]
    assert [row["status"] for row in result] == [
        "stored",
        "failed",
        "stored",
    ]
    assert [row["replaces"] for row in result] == [None, 1, 2]
    assert result[1]["failure_reason"] == "replaced_not_found"
    assert result[1]["sha256"] is None
    assert result[2]["sha256"] == "c" * 64
    assert all(row["run_id"] == "TEST-RUN-003" for row in result)


def test_select_latest_successfully_stored_photo():
    from rebom.storage.silver.photo_version_history import (
        select_current_photo_version,
    )

    history = [
        {
            "desertion_no": "TEST-001",
            "image_slot": 1,
            "version": 1,
            "status": "stored",
            "sha256": "a" * 64,
        },
        {
            "desertion_no": "TEST-001",
            "image_slot": 1,
            "version": 2,
            "status": "failed",
            "sha256": None,
        },
        {
            "desertion_no": "TEST-001",
            "image_slot": 1,
            "version": 3,
            "status": "stored",
            "sha256": "c" * 64,
        },
    ]

    current = select_current_photo_version(history)

    assert len(current) == 1
    assert current[("TEST-001", 1)]["version"] == 3
    assert current[("TEST-001", 1)]["sha256"] == "c" * 64


def test_failed_replacement_keeps_previous_photo():
    from rebom.storage.silver.photo_version_history import (
        select_current_photo_version,
    )

    history = [
        {
            "desertion_no": "TEST-001",
            "image_slot": 1,
            "version": 1,
            "status": "stored",
            "sha256": "a" * 64,
        },
        {
            "desertion_no": "TEST-001",
            "image_slot": 1,
            "version": 2,
            "status": "failed",
            "sha256": None,
        },
    ]

    current = select_current_photo_version(history)

    assert len(current) == 1
    assert current[("TEST-001", 1)]["version"] == 1
    assert current[("TEST-001", 1)]["sha256"] == "a" * 64


def test_photo_history_new_version_insert():
    from rebom.storage.silver.photo_version_history import (
        plan_photo_history_inserts,
    )

    incoming = {
        "desertion_no": "TEST-001",
        "image_slot": 1,
        "version": 3,
        "status": "stored",
        "sha256": "c" * 64,
        "run_id": "RUN-003",
    }

    result = plan_photo_history_inserts([incoming], [])

    assert result["insert_rows"] == [incoming]
    assert result["insert_count"] == 1
    assert result["skip_count"] == 0


def test_photo_history_same_version_rerun_skips():
    from rebom.storage.silver.photo_version_history import (
        plan_photo_history_inserts,
    )

    existing = {
        "desertion_no": "TEST-001",
        "image_slot": 1,
        "version": 3,
        "status": "stored",
        "sha256": "c" * 64,
        "run_id": "RUN-003",
    }

    incoming = existing.copy()
    incoming["run_id"] = "RUN-004"

    result = plan_photo_history_inserts([incoming], [existing])

    assert result["insert_rows"] == []
    assert result["insert_count"] == 0
    assert result["skip_count"] == 1


def test_photo_history_same_version_hash_conflict():
    from rebom.storage.silver.photo_version_history import (
        plan_photo_history_inserts,
    )

    existing = {
        "desertion_no": "TEST-001",
        "image_slot": 1,
        "version": 3,
        "status": "stored",
        "sha256": "c" * 64,
        "run_id": "RUN-003",
    }

    incoming = existing.copy()
    incoming["sha256"] = "d" * 64

    with pytest.raises(ValueError, match="사진 이력 데이터 충돌"):
        plan_photo_history_inserts([incoming], [existing])


def test_photo_history_failed_to_stored_updates_once():
    from rebom.storage.silver.photo_version_history import plan_photo_history_inserts

    failed = {
        "desertion_no": "TEST-001", "image_slot": 1, "version": 2,
        "replaces": 1, "first_seen_at": "2026-11-17T00:00:00Z",
        "source_url": "https://example.com/v2.jpg", "status": "failed",
        "failure_reason": "replaced_not_found", "original_s3_key": None,
        "sha256": None, "run_id": "RUN-001",
    }
    recovered = dict(failed, status="stored", failure_reason=None,
                     original_s3_key="bronze/v2.jpg", sha256="b" * 64,
                     run_id="RUN-002")

    plan = plan_photo_history_inserts([recovered], [failed])
    assert plan["insert_count"] == 0
    assert plan["update_count"] == 1
    assert plan["update_rows"] == [recovered]

    rerun = plan_photo_history_inserts([recovered], [recovered])
    assert rerun["insert_count"] == 0
    assert rerun["update_count"] == 0
    assert rerun["skip_count"] == 1


def test_photo_history_stored_to_failed_rejected():
    from rebom.storage.silver.photo_version_history import plan_photo_history_inserts

    stored = {
        "desertion_no": "TEST-001", "image_slot": 1, "version": 2,
        "status": "stored", "failure_reason": None,
        "original_s3_key": "bronze/v2.jpg", "sha256": "b" * 64,
    }
    failed = dict(stored, status="failed", failure_reason="replaced_not_found",
                  original_s3_key=None, sha256=None)
    with pytest.raises(ValueError, match="사진 이력 데이터 충돌"):
        plan_photo_history_inserts([failed], [stored])


def test_photo_history_recovery_metadata_conflict_rejected():
    from rebom.storage.silver.photo_version_history import plan_photo_history_inserts

    failed = {
        "desertion_no": "TEST-001", "image_slot": 1, "version": 2,
        "source_url": "https://example.com/v2.jpg", "status": "failed",
        "failure_reason": "replaced_not_found",
    }
    recovered = dict(failed, status="stored", failure_reason=None,
                     source_url="https://example.com/other.jpg",
                     original_s3_key="bronze/v2.jpg", sha256="b" * 64)
    with pytest.raises(ValueError, match="사진 이력 데이터 충돌"):
        plan_photo_history_inserts([recovered], [failed])


def test_photo_history_recovery_selects_recovered_version():
    from rebom.storage.silver.photo_version_history import (
        plan_photo_history_inserts, select_current_photo_version,
    )

    v1 = {
        "desertion_no": "TEST-001", "image_slot": 1, "version": 1,
        "status": "stored", "sha256": "a" * 64,
    }
    failed_v2 = {
        "desertion_no": "TEST-001", "image_slot": 1, "version": 2,
        "status": "failed", "failure_reason": "replaced_not_found",
    }
    stored_v2 = dict(failed_v2, status="stored", failure_reason=None,
                     original_s3_key="bronze/v2.jpg", sha256="b" * 64)

    assert select_current_photo_version([v1, failed_v2])[("TEST-001", 1)]["version"] == 1
    changes = plan_photo_history_inserts([stored_v2], [v1, failed_v2])
    assert changes["update_count"] == 1
    assert select_current_photo_version([v1, stored_v2])[("TEST-001", 1)]["version"] == 2

def test_photo_current_recovery_after_history_saved():
    from rebom.storage.silver.photo_version_history import (
        plan_photo_current_updates,
    )

    def photo(version, status, sha256):
        return {
            "desertion_no": "TEST-001",
            "image_slot": 1,
            "version": version,
            "status": status,
            "original_s3_key": f"bronze/photo_v{version}.jpg" if sha256 else None,
            "serving_s3_key": f"images/photo_v{version}.webp" if sha256 else None,
            "thumb_s3_key": f"images/photo_v{version}_thumb.webp" if sha256 else None,
            "sha256": sha256,
        }

    history = [
        photo(1, "stored", "a" * 64),
        photo(2, "failed", None),
        photo(3, "stored", "c" * 64),
    ]

    current = [
        {
            key: value
            for key, value in history[0].items()
            if key not in ("version", "status")
        }
    ]

    first = plan_photo_current_updates(current, history)

    assert first["changed_count"] == 1
    assert first["updates"][0]["sha256"] == "c" * 64
    assert first["updates"][0]["serving_s3_key"] == "images/photo_v3.webp"

    second = plan_photo_current_updates(first["updates"], history)

    assert second["changed_count"] == 0
    assert second["updates"] == []


def test_photo_current_recovery_rejects_unknown_hash():
    from rebom.storage.silver.photo_version_history import (
        plan_photo_current_updates,
    )

    history = [{
        "desertion_no": "TEST-001",
        "image_slot": 1,
        "version": 1,
        "status": "stored",
        "original_s3_key": "bronze/v1.jpg",
        "serving_s3_key": "images/v1.webp",
        "thumb_s3_key": "images/v1_thumb.webp",
        "sha256": "a" * 64,
    }]

    current = [{
        "desertion_no": "TEST-001",
        "image_slot": 1,
        "original_s3_key": "bronze/unknown.jpg",
        "serving_s3_key": "images/unknown.webp",
        "thumb_s3_key": "images/unknown_thumb.webp",
        "sha256": "z" * 64,
    }]

    with pytest.raises(RuntimeError, match="현재 사진과 저장 이력 불일치"):
        plan_photo_current_updates(current, history)


def test_photo_current_empty_slot_fills_v1_stored():
    from rebom.storage.silver.photo_version_history import plan_photo_current_updates

    current = [{
        "desertion_no": "TEST-EMPTY-001",
        "image_slot": 1,
        "original_s3_key": None,
        "serving_s3_key": None,
        "thumb_s3_key": None,
        "sha256": None,
    }]

    history = [{
        "desertion_no": "TEST-EMPTY-001",
        "image_slot": 1,
        "version": 1,
        "status": "stored",
        "original_s3_key": "bronze/v1.jpg",
        "serving_s3_key": "images/v1.webp",
        "thumb_s3_key": "images/v1_thumb.webp",
        "sha256": "a" * 64,
    }]

    result = plan_photo_current_updates(current, history)

    assert result["changed_count"] == 1
    assert result["updates"][0]["sha256"] == "a" * 64
    assert result["updates"][0]["serving_s3_key"] == "images/v1.webp"


def test_photo_current_empty_slot_skips_failed_and_fills_v2():
    from rebom.storage.silver.photo_version_history import plan_photo_current_updates

    current = [{
        "desertion_no": "TEST-EMPTY-002",
        "image_slot": 1,
        "original_s3_key": None,
        "serving_s3_key": None,
        "thumb_s3_key": None,
        "sha256": None,
    }]

    history = [
        {
            "desertion_no": "TEST-EMPTY-002",
            "image_slot": 1,
            "version": 1,
            "status": "failed",
            "original_s3_key": None,
            "serving_s3_key": None,
            "thumb_s3_key": None,
            "sha256": None,
        },
        {
            "desertion_no": "TEST-EMPTY-002",
            "image_slot": 1,
            "version": 2,
            "status": "stored",
            "original_s3_key": "bronze/v2.jpg",
            "serving_s3_key": "images/v2.webp",
            "thumb_s3_key": "images/v2_thumb.webp",
            "sha256": "b" * 64,
        },
    ]

    result = plan_photo_current_updates(current, history)

    assert result["changed_count"] == 1
    assert result["updates"][0]["sha256"] == "b" * 64
    assert result["updates"][0]["serving_s3_key"] == "images/v2.webp"


def test_photo_current_partial_none_still_rejected():
    from rebom.storage.silver.photo_version_history import plan_photo_current_updates

    current = [{
        "desertion_no": "TEST-PARTIAL-001",
        "image_slot": 1,
        "original_s3_key": None,
        "serving_s3_key": "images/unknown.webp",
        "thumb_s3_key": None,
        "sha256": None,
    }]

    history = [{
        "desertion_no": "TEST-PARTIAL-001",
        "image_slot": 1,
        "version": 1,
        "status": "stored",
        "original_s3_key": "bronze/v1.jpg",
        "serving_s3_key": "images/v1.webp",
        "thumb_s3_key": "images/v1_thumb.webp",
        "sha256": "a" * 64,
    }]

    with pytest.raises(RuntimeError, match="현재 사진과 저장 이력 불일치"):
        plan_photo_current_updates(current, history)


def test_photo_version_replacement_updates_url_and_is_idempotent():
    from rebom.storage.silver.photo_version_history import (
        plan_photo_current_updates,
    )

    current = {
        "desertion_no": "TEST-001",
        "image_slot": 1,
        "source_url": "https://example.com/v1.jpg",
        "original_s3_key": "bronze/v1.jpg",
        "serving_s3_key": "images/v1.webp",
        "thumb_s3_key": "images/v1_thumb.webp",
        "sha256": "a" * 64,
    }

    v1 = {
        **current,
        "version": 1,
        "status": "stored",
    }

    v2 = {
        **current,
        "version": 2,
        "status": "stored",
        "source_url": "https://example.com/v2.jpg",
        "original_s3_key": "bronze/v2.jpg",
        "serving_s3_key": "images/v2.webp",
        "thumb_s3_key": "images/v2_thumb.webp",
        "sha256": "b" * 64,
    }

    first = plan_photo_current_updates([current], [v1, v2])

    assert first["changed_count"] == 1

    updated = first["updates"][0]

    assert updated["source_url"] == v2["source_url"]
    assert updated["original_s3_key"] == v2["original_s3_key"]
    assert updated["serving_s3_key"] == v2["serving_s3_key"]
    assert updated["thumb_s3_key"] == v2["thumb_s3_key"]
    assert updated["sha256"] == v2["sha256"]

    second = plan_photo_current_updates([updated], [v1, v2])

    assert second["changed_count"] == 0
    assert second["updates"] == []

def test_legacy_manifest_preserves_current_source_url():
    from rebom.storage.silver.photo_version_history import (
        plan_photo_current_updates,
    )

    current = {
        "desertion_no": "TEST-LEGACY",
        "image_slot": 1,
        "source_url": "https://example.com/existing.jpg",
        "original_s3_key": None,
        "serving_s3_key": None,
        "thumb_s3_key": None,
        "sha256": None,
    }

    legacy_v1 = {
        **current,
        "version": 1,
        "status": "stored",
        "source_url": None,
        "original_s3_key": "bronze/v1.jpg",
        "serving_s3_key": "images/v1.webp",
        "thumb_s3_key": "images/v1_thumb.webp",
        "sha256": "a" * 64,
    }

    first = plan_photo_current_updates([current], [legacy_v1])

    assert first["changed_count"] == 1
    updated = first["updates"][0]
    assert updated["source_url"] == current["source_url"]
    assert updated["sha256"] == legacy_v1["sha256"]

    second = plan_photo_current_updates([updated], [legacy_v1])

    assert second["changed_count"] == 0
    assert second["updates"] == []


def test_failed_v2_with_legacy_v1_keeps_current_url():
    from rebom.storage.silver.photo_version_history import (
        plan_photo_current_updates,
    )

    current = {
        "desertion_no": "TEST-FAILED-V2",
        "image_slot": 1,
        "source_url": "https://example.com/v1.jpg",
        "original_s3_key": "bronze/v1.jpg",
        "serving_s3_key": "images/v1.webp",
        "thumb_s3_key": "images/v1_thumb.webp",
        "sha256": "a" * 64,
    }

    v1 = {
        **current,
        "version": 1,
        "status": "stored",
        "source_url": None,
    }

    v2 = {
        **current,
        "version": 2,
        "status": "failed",
        "source_url": "https://example.com/v2.jpg",
        "failure_reason": "replaced_not_found",
    }

    result = plan_photo_current_updates([current], [v1, v2])

    assert result["changed_count"] == 0
    assert result["updates"] == []

def test_new_photo_v1_insert_and_rerun():
    from rebom.storage.silver.photo_version_history import (
        plan_photo_current_updates,
    )

    v1 = {
        "desertion_no": "TEST-NEW-001",
        "image_slot": 1,
        "version": 1,
        "replaces": None,
        "status": "stored",
        "source_url": "https://example.com/v1.jpg",
        "original_s3_key": "bronze/rescue/images/TEST-NEW-001/popfile1.jpg",
        "serving_s3_key": "images/TEST-NEW-001/1.webp",
        "thumb_s3_key": "images/TEST-NEW-001/1_thumb.webp",
        "sha256": "a" * 64,
    }

    first = plan_photo_current_updates([], [v1])

    assert first["changed_count"] == 1
    assert len(first["inserts"]) == 1
    assert first["updates"] == []

    second = plan_photo_current_updates(first["inserts"], [v1])

    assert second["changed_count"] == 0
    assert second["inserts"] == []
    assert second["updates"] == []


def test_new_photo_v2_without_previous_version_blocked():
    from rebom.storage.silver.photo_version_history import (
        plan_photo_current_updates,
    )

    v2 = {
        "desertion_no": "TEST-NEW-002",
        "image_slot": 1,
        "version": 2,
        "replaces": 1,
        "status": "stored",
        "source_url": "https://example.com/v2.jpg",
        "original_s3_key": "bronze/v2.jpg",
        "serving_s3_key": "images/v2.webp",
        "thumb_s3_key": "images/v2_thumb.webp",
        "sha256": "b" * 64,
    }

    with pytest.raises(RuntimeError, match="MISSING_PREVIOUS_VERSION"):
        plan_photo_current_updates([], [v2])


def test_new_photo_insert_and_existing_photo_update_together():
    from rebom.storage.silver.photo_version_history import (
        plan_photo_current_updates,
    )

    existing = {
        "desertion_no": "TEST-EXISTING",
        "image_slot": 1,
        "source_url": "https://example.com/old.jpg",
        "original_s3_key": None,
        "serving_s3_key": None,
        "thumb_s3_key": None,
        "sha256": None,
    }

    stored = {
        **existing,
        "version": 1,
        "status": "stored",
        "original_s3_key": "bronze/old.jpg",
        "serving_s3_key": "images/old.webp",
        "thumb_s3_key": "images/old_thumb.webp",
        "sha256": "a" * 64,
    }

    new = {
        **stored,
        "desertion_no": "TEST-NEW-003",
        "source_url": "https://example.com/new.jpg",
    }

    plan = plan_photo_current_updates([existing], [stored, new])

    assert plan["changed_count"] == 2
    assert len(plan["inserts"]) == 1
    assert len(plan["updates"]) == 1

    after = plan["updates"] + plan["inserts"]
    replay = plan_photo_current_updates(after, [stored, new])

    assert replay["changed_count"] == 0
