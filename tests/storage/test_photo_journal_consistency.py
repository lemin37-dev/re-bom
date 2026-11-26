"""Journal-Iceberg 사진 정합성 로컬 오류 탐지 테스트."""

import copy
import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts/storage/rebom_check_photo_journal_consistency.py"
)

spec = importlib.util.spec_from_file_location(
    "rebom_check_photo_journal_consistency",
    SCRIPT,
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def make_photo(slot):
    return {
        "desertion_no": "TEST-001",
        "image_slot": slot,
        "original_s3_key": f"bronze/test/{slot}.jpg",
        "serving_s3_key": f"images/test/{slot}.webp",
        "thumb_s3_key": f"images/test/{slot}_thumb.webp",
        "sha256": "a" * 64,
    }


def test_normal_rows_pass():
    photos = [make_photo(1), make_photo(2)]
    index = {
        (p["desertion_no"], p["image_slot"]): copy.deepcopy(p)
        for p in photos
    }

    mismatches, checked = module.check_photo_rows(photos, index)

    assert mismatches == []
    assert checked == 2


def test_missing_iceberg_row_detected():
    photos = [make_photo(1), make_photo(2)]
    index = {
        ("TEST-001", 1): copy.deepcopy(photos[0]),
    }

    mismatches, checked = module.check_photo_rows(photos, index)

    assert checked == 1
    assert len(mismatches) == 1
    assert "Iceberg 행 없음" in mismatches[0]


def test_sha256_mismatch_detected():
    photos = [make_photo(1)]
    wrong = copy.deepcopy(photos[0])
    wrong["sha256"] = "b" * 64

    index = {("TEST-001", 1): wrong}

    mismatches, checked = module.check_photo_rows(photos, index)

    assert checked == 1
    assert len(mismatches) == 1
    assert "sha256 불일치" in mismatches[0]
