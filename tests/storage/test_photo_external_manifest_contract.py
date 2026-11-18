"""A팀 #33 생성 Manifest와 B팀 Silver 버전 이력 호환성 검증."""

from pathlib import Path

from rebom.storage.silver.load_photo_manifest import load_photo_manifest
from rebom.storage.silver.photo_version_history import (
    build_photo_version_history_rows,
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
