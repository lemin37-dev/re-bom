"""A팀 #33 생성 Manifest와 B팀 Silver 버전 이력 호환성 검증."""

import json
from pathlib import Path

import pytest

from rebom.storage.silver.load_photo_manifest import load_photo_manifest
from rebom.storage.silver.photo_version_history import (
    build_photo_version_history_rows,
    plan_photo_current_updates,
    plan_photo_history_inserts,
    select_current_photo_version,
)


FIXTURE_DIR = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "sample"
    / "photo_replacement"
)


def test_a_team_photo_replacement_manifests():
    filenames = [
        "1_v1_success.json",
        "2_v2_replaced_not_found.json",
        "3_v3_success.json",
    ]

    manifests = [
        load_photo_manifest(FIXTURE_DIR / filename)
        for filename in filenames
    ]

    assert [m["run_id"] for m in manifests] == [
        "p0", "p1", "p2"
    ]

    assert [len(m["rows"]) for m in manifests] == [
        1, 0, 1
    ]

    assert [
        len(m.get("failed_rows", []))
        for m in manifests
    ] == [0, 1, 0]

    history = []

    for manifest in manifests:
        history.extend(
            build_photo_version_history_rows(manifest)
        )

    assert len(history) == 3
    assert [row["version"] for row in history] == [1, 2, 3]
    assert [row["status"] for row in history] == [
        "stored", "failed", "stored"
    ]
    assert [row["replaces"] for row in history] == [
        None, 1, 2
    ]

    assert history[1]["failure_reason"] == "replaced_not_found"
    assert history[1]["source_url"] == "http://x/b"
    assert history[1]["sha256"] is None

    current = select_current_photo_version(history)

    assert len(current) == 1
    assert current[("111", 1)]["version"] == 3
    assert current[("111", 1)]["serving_s3_key"] == (
        "images/111/1_v003.webp"
    )

    assert history[0]["sha256"] == history[2]["sha256"]
    assert (
        history[0]["serving_s3_key"]
        != history[2]["serving_s3_key"]
    )


@pytest.fixture
def initial_photo_failure_manifest():
    return {
        "run_id": "failed-v1",
        "bronze_run_id": "bronze-v1",
        "status": "ok",
        "files": [],
        "failed": [{
            "desertionNo": "111",
            "slot": 1,
            "version": 1,
            "source_url": "https://example.com/v1.jpg",
            "status": "gave_up",
            "reason": "not_found",
        }],
    }


def test_gave_up_v1_allows_v2_to_become_current(tmp_path, initial_photo_failure_manifest):
    path = tmp_path / "failed-v1.json"
    path.write_text(json.dumps(initial_photo_failure_manifest), encoding="utf-8")
    failed_rows = build_photo_version_history_rows(load_photo_manifest(path))
    failed, = plan_photo_history_inserts(failed_rows, [])["insert_rows"]
    assert failed["status"] == "failed"
    assert failed["failure_reason"] == "not_found"
    assert failed["sha256"] is None
    assert plan_photo_current_updates([], [failed])["changed_count"] == 0

    success = json.loads((FIXTURE_DIR / "3_v3_success.json").read_text(encoding="utf-8"))
    photo = success["files"][0]
    photo.update(version=2, replaces=1)
    photo["original"] = photo["original"].replace("_v003", "_v002")
    for field in ("image", "thumb"):
        photo["serving"][field] = photo["serving"][field].replace("_v003", "_v002")
    path = tmp_path / "stored-v2.json"
    path.write_text(json.dumps(success), encoding="utf-8")
    stored_rows = build_photo_version_history_rows(load_photo_manifest(path))
    stored, = plan_photo_history_inserts(stored_rows, [failed])["insert_rows"]

    with pytest.raises(RuntimeError, match="MISSING_PREVIOUS_VERSION"):
        plan_photo_current_updates([], [dict(failed, image_slot=2), stored])
    with pytest.raises(RuntimeError, match="MISSING_PREVIOUS_VERSION"):
        plan_photo_current_updates([], [failed, dict(stored, replaces=2)])

    current, = plan_photo_current_updates([], [failed, stored])["inserts"]
    assert select_current_photo_version([failed, stored])[("111", 1)] == stored
    assert current["original_s3_key"] == photo["original"]


@pytest.mark.parametrize(
    ("change", "error"),
    [("duplicate", "중복 버전"), ("replaces", "replaces 오류")],
)
def test_initial_failure_manifest_rejects_invalid_history(
    tmp_path, initial_photo_failure_manifest, change, error,
):
    failures = initial_photo_failure_manifest["failed"]
    if change == "duplicate":
        failures.append(failures[0].copy())
    else:
        failures[0]["replaces"] = 1
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(initial_photo_failure_manifest), encoding="utf-8")

    with pytest.raises(ValueError, match=error):
        build_photo_version_history_rows(load_photo_manifest(path))
