import sys
from unittest.mock import patch

import pytest

from rebom.storage.silver import run_silver_once


RUN_ID = "20261111T111811-d6a30f"
BUCKET = "rebom-data-559987919299"


@pytest.mark.parametrize("enabled", [False, True])
def test_photo_manifest_integration_is_opt_in(enabled):
    args = [
        "run_silver_once",
        "--run-id", RUN_ID,
        "--bucket", BUCKET,
        "--environment", "test",
    ]

    if enabled:
        args.append("--check-photo-manifests")

    with (
        patch.object(sys, "argv", args),
        patch.object(run_silver_once.boto3, "client"),
        patch.object(
            run_silver_once,
            "fetch_run_inputs",
            return_value={
                "run_id": RUN_ID,
                "bronze_manifest": "mock-bronze.json",
                "event_manifest": "mock-events.json",
            },
        ),
        patch.object(run_silver_once.subprocess, "run") as mocked_run,
    ):
        run_silver_once.main()

    calls = mocked_run.call_args_list

    photo_calls = [
        call for call in calls
        if "rebom_auto_process_photo_manifests.py"
        in " ".join(map(str, call.args[0]))
    ]

    assert len(calls) == (2 if enabled else 1)
    assert len(photo_calls) == (1 if enabled else 0)

    if enabled:
        command = photo_calls[0].args[0]
        env = photo_calls[0].kwargs["env"]

        assert "--bronze-run-id" in command
        assert RUN_ID in command
        assert "--apply" not in command
        assert "--defer-missing-manifest" in command
        assert env["REBOM_PHOTO_ICEBERG_TABLE"] == (
            "rebom_silver.silver_animal_image_auto_b_test"
        )
        assert env["REBOM_PHOTO_HISTORY_TABLE"] == (
            "rebom_silver.silver_animal_image_version_history_auto_b_test"
        )


def test_photo_manifest_check_rejects_apply():
    args = [
        "run_silver_once",
        "--run-id", RUN_ID,
        "--bucket", BUCKET,
        "--environment", "test",
        "--apply",
        "--check-photo-manifests",
    ]

    with patch.object(sys, "argv", args):
        with pytest.raises(SystemExit) as error:
            run_silver_once.main()

    assert error.value.code == 2

def test_photo_manifest_failure_propagates_after_silver():
    import subprocess

    args = [
        "run_silver_once",
        "--run-id", RUN_ID,
        "--bucket", BUCKET,
        "--environment", "test",
        "--check-photo-manifests",
    ]

    with (
        patch.object(sys, "argv", args),
        patch.object(run_silver_once.boto3, "client"),
        patch.object(
            run_silver_once,
            "fetch_run_inputs",
            return_value={
                "run_id": RUN_ID,
                "bronze_manifest": "mock-bronze.json",
                "event_manifest": "mock-events.json",
            },
        ),
        patch.object(
            run_silver_once.subprocess,
            "run",
            side_effect=[
                None,
                subprocess.CalledProcessError(1, "photo-manifest"),
            ],
        ) as mocked_run,
    ):
        with pytest.raises(subprocess.CalledProcessError):
            run_silver_once.main()

    assert mocked_run.call_count == 2

def test_silver_failure_prevents_photo_processing():
    import subprocess

    args = [
        "run_silver_once",
        "--run-id", RUN_ID,
        "--bucket", BUCKET,
        "--environment", "test",
        "--check-photo-manifests",
    ]

    with (
        patch.object(sys, "argv", args),
        patch.object(run_silver_once.boto3, "client"),
        patch.object(
            run_silver_once,
            "fetch_run_inputs",
            return_value={
                "run_id": RUN_ID,
                "bronze_manifest": "mock-bronze.json",
                "event_manifest": "mock-events.json",
            },
        ),
        patch.object(
            run_silver_once.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, "silver"),
        ) as mocked_run,
    ):
        with pytest.raises(subprocess.CalledProcessError):
            run_silver_once.main()

    assert mocked_run.call_count == 1
