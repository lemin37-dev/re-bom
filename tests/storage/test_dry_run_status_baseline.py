from unittest.mock import Mock, patch

import pytest

from rebom.storage.silver.dry_run_baseline import build_dry_run_baseline
from rebom.storage.silver.dry_run_previous_bronze import fetch_previous_day_bronze
from scripts.storage.rebom_run_aws_real_silver_test import prepare_new


CURRENT = "20261119T030013-e4e83a"
PREVIOUS = "20261118T030011-6f2d1f"
PREVIOUS_KEY = (
    "bronze/rescue/raw/dt=2026-11-18/"
    f"run_id={PREVIOUS}/manifest.json"
)


def event(key):
    return {
        "event": "vanished",
        "desertionNo": key,
        "confirmed_by": "complete_range",
    }


def test_baseline_adds_missing_confirmed_notice():
    existing = [{"desertion_no": "existing"}]
    bronze = {
        "rows_by_kind": {
            "updated": [{
                "desertionNo": "missing",
                "processState": "\ubcf4\ud638\uc911",
                "happenDt": "20261118",
            }]
        }
    }

    result = build_dry_run_baseline(
        existing, bronze, [event("missing"), event("existing")]
    )

    assert len(result) == 2
    assert result[0] == existing[0]
    assert result[1]["desertion_no"] == "missing"
    assert result[1]["status_group"] == "PROTECTING"


def test_baseline_rejects_missing_previous_notice():
    with pytest.raises(ValueError, match="DRY_RUN_PREVIOUS_BRONZE_MISSING"):
        build_dry_run_baseline(
            [],
            {"rows_by_kind": {"updated": []}},
            [event("missing")],
        )


def test_previous_bronze_rejects_multiple_manifests(tmp_path):
    s3 = Mock()
    s3.get_paginator.return_value.paginate.return_value = [{
        "Contents": [
            {"Key": PREVIOUS_KEY},
            {"Key": PREVIOUS_KEY.replace(PREVIOUS, "20261118T040000-abcdef")},
        ]
    }]

    with pytest.raises(RuntimeError, match="PREVIOUS_BRONZE_MANIFEST_COUNT: 2"):
        fetch_previous_day_bronze(s3, "bucket", CURRENT, tmp_path)

    s3.download_file.assert_not_called()


def test_previous_bronze_loads_verified_run(tmp_path):
    s3 = Mock()
    s3.get_paginator.return_value.paginate.return_value = [{
        "Contents": [{"Key": PREVIOUS_KEY}]
    }]

    with patch(
        "rebom.storage.silver.dry_run_previous_bronze.load_bronze_run",
        return_value={"run_id": PREVIOUS},
    ):
        result = fetch_previous_day_bronze(
            s3, "bucket", CURRENT, tmp_path
        )

    assert result["run_id"] == PREVIOUS
    assert s3.download_file.call_count == 4


def test_apply_rejects_virtual_baseline():
    with pytest.raises(
        RuntimeError, match="DRY_RUN_BASELINE_FORBIDDEN_FOR_APPLY"
    ):
        prepare_new(
            None, None, None, None,
            apply=True,
            dry_run_baseline_rows=[],
        )
