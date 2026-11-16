import json

import pytest

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
    assert result["rows"][0]["original_s3_key"] == "bronze/rescue/images/TEST-001/popfile1_v002.jpg"
    assert result["rows"][0]["serving_s3_key"] == "images/TEST-001/1_v002.webp"
    assert result["rows"][0]["thumb_s3_key"] == "images/TEST-001/1_v002_thumb.webp"


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


def test_photo_v1_backward_compatible(tmp_path):
    manifest = {
        "run_id": "TEST-PHOTO-V1",
        "bronze_run_id": "TEST-BRONZE-V1",
        "status": "ok",
        "files": [
            {
                "desertionNo": "TEST-001",
                "slot": 1,
                "original": "bronze/rescue/images/TEST-001/popfile1.jpg",
                "ext": "jpg",
                "sha256": "a" * 64,
                "serving": {
                    "image": "images/TEST-001/1.webp",
                    "thumb": "images/TEST-001/1_thumb.webp",
                },
            }
        ],
    }

    path = tmp_path / "v1.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    result = load_photo_manifest(path)

    assert len(result["rows"]) == 1
    assert result["rows"][0]["version"] == 1
    assert result["rows"][0]["original_s3_key"].endswith("popfile1.jpg")


def test_photo_version_path_mismatch(tmp_path):
    manifest = {
        "run_id": "TEST-PHOTO-MISMATCH",
        "bronze_run_id": "TEST-BRONZE-V1",
        "status": "ok",
        "files": [
            {
                "desertionNo": "TEST-001",
                "slot": 1,
                "original": "bronze/rescue/images/TEST-001/popfile1.jpg",
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

    path = tmp_path / "mismatch.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError):
        load_photo_manifest(path)