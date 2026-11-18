"""?? Manifest ?? ??? ?? ???."""

import io
import json
import sys
from unittest.mock import Mock

import pytest

from scripts.storage import rebom_auto_process_photo_manifests as script


BUCKET = "rebom-data-559987919299"
BRONZE_ID = "20261118T030011-6f2d1f"
PHOTO_ID = "20261118T030135-82547e"

PREFIX = "bench_tmp/b-storage-test/journals/photo_auto_runs"

MANIFEST_KEY = (
    "bronze/rescue/image_runs/dt=2026-11-18/"
    f"run_id={PHOTO_ID}/manifest.json"
)

CONFIG = {
    "bucket": BUCKET,
    "region": "ap-northeast-2",
    "profile": "rebom-seoul",
    "table_name": "rebom_silver.silver_animal_image_auto_b_test",
    "expected_location": (
        f"s3://{BUCKET}/bench_tmp/b-storage-test/"
        "iceberg/silver_animal_image_auto_b_test"
    ),
    "history_table_name": (
        "rebom_silver.silver_animal_image_version_history_auto_b_test"
    ),
    "expected_history_location": (
        f"s3://{BUCKET}/bench_tmp/b-storage-test/"
        "iceberg/silver_animal_image_version_history_auto_b_test"
    ),
    "journal_prefix": PREFIX,
    "manifest_prefix": "bronze/rescue/image_runs/",
    "stale_seconds": 3600,
}


@pytest.fixture
def fake_runtime(monkeypatch):
    s3 = Mock()
    sts = Mock()
    sts.get_caller_identity.return_value = {
        "Account": script.EXPECTED_ACCOUNT
    }

    session = Mock()
    session.client.side_effect = lambda name: {
        "sts": sts,
        "s3": s3,
    }[name]

    monkeypatch.setattr(
        script.boto3, "Session", lambda **kwargs: session
    )
    monkeypatch.setattr(
        script, "get_aws_catalog", lambda **kwargs: Mock()
    )
    monkeypatch.setattr(
        script, "get_config", lambda: dict(CONFIG)
    )
    monkeypatch.setattr(
        script, "validate_config", lambda config: None
    )

    return s3


def set_apply_args(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rebom_auto_process_photo_manifests.py",
            "--apply",
            "--bronze-run-id", BRONZE_ID,
            "--record-missing-manifest",
        ],
    )


def test_group_pending_manifests_includes_previous_run(monkeypatch):
    previous = "20261117T030011-8d0526"

    monkeypatch.setattr(
        script, "list_pending_manifests",
        lambda *args: [previous],
    )

    s3 = Mock()
    s3.get_object.return_value = {
        "Body": io.BytesIO(
            json.dumps({
                "bronze_run_id": BRONZE_ID
            }).encode("utf-8")
        )
    }

    grouped = script.group_pending_manifests(
        s3, BUCKET, [MANIFEST_KEY], BRONZE_ID, PREFIX
    )

    assert grouped[BRONZE_ID] == [MANIFEST_KEY]
    assert grouped[previous] == []


def test_missing_manifest_creates_pending(
    monkeypatch, fake_runtime
):
    set_apply_args(monkeypatch)

    monkeypatch.setattr(
        script, "find_manifests", lambda *args: []
    )
    monkeypatch.setattr(
        script, "list_pending_manifests", lambda *args: []
    )

    save = Mock(return_value="CREATED")
    process = Mock()
    resolve = Mock()

    monkeypatch.setattr(script, "save_pending_manifest", save)
    monkeypatch.setattr(script, "process_photo_manifest", process)
    monkeypatch.setattr(script, "mark_pending_resolved", resolve)

    script.main()

    save.assert_called_once_with(
        fake_runtime, BUCKET, PREFIX, BRONZE_ID
    )
    process.assert_not_called()
    resolve.assert_not_called()


def test_recovered_manifest_resolves_pending(
    monkeypatch, fake_runtime
):
    set_apply_args(monkeypatch)

    fake_runtime.get_object.return_value = {
        "Body": io.BytesIO(
            json.dumps({
                "bronze_run_id": BRONZE_ID
            }).encode("utf-8")
        )
    }

    monkeypatch.setattr(
        script, "find_manifests", lambda *args: [MANIFEST_KEY]
    )
    monkeypatch.setattr(
        script, "list_pending_manifests", lambda *args: [BRONZE_ID]
    )
    monkeypatch.setattr(
        script,
        "evaluate_photo_staleness",
        lambda **kwargs: {
            "decision": "READY_FINAL",
            "run_id": PHOTO_ID,
        },
    )

    process = Mock(return_value={
        "run_id": PHOTO_ID,
        "result": "APPLIED_AND_VERIFIED",
    })
    resolve = Mock(return_value="CREATED")
    save = Mock()

    monkeypatch.setattr(script, "process_photo_manifest", process)
    monkeypatch.setattr(script, "mark_pending_resolved", resolve)
    monkeypatch.setattr(script, "save_pending_manifest", save)

    script.main()

    assert process.call_args.kwargs["apply"] is True

    resolve.assert_called_once_with(
        fake_runtime,
        BUCKET,
        PREFIX,
        BRONZE_ID,
        [PHOTO_ID],
    )
    save.assert_not_called()


def test_deferred_manifest_remains_pending(
    monkeypatch, fake_runtime
):
    set_apply_args(monkeypatch)

    fake_runtime.get_object.return_value = {
        "Body": io.BytesIO(
            json.dumps({
                "bronze_run_id": BRONZE_ID
            }).encode("utf-8")
        )
    }

    monkeypatch.setattr(
        script, "find_manifests", lambda *args: [MANIFEST_KEY]
    )
    monkeypatch.setattr(
        script, "list_pending_manifests", lambda *args: [BRONZE_ID]
    )
    monkeypatch.setattr(
        script,
        "evaluate_photo_staleness",
        lambda **kwargs: {
            "decision": "DEFER",
            "run_id": PHOTO_ID,
        },
    )

    process = Mock()
    resolve = Mock()

    monkeypatch.setattr(script, "process_photo_manifest", process)
    monkeypatch.setattr(script, "mark_pending_resolved", resolve)

    script.main()

    process.assert_not_called()
    resolve.assert_not_called()


@pytest.mark.parametrize(
    "flags,error",
    [
        (
            ["--bronze-run-id", BRONZE_ID,
             "--record-missing-manifest"],
            "PHOTO_PENDING_APPLY_REQUIRES_BRONZE_RUN_ID",
        ),
        (
            ["--apply", "--record-missing-manifest"],
            "PHOTO_PENDING_APPLY_REQUIRES_BRONZE_RUN_ID",
        ),
        (
            ["--apply", "--bronze-run-id", BRONZE_ID,
             "--record-missing-manifest",
             "--defer-missing-manifest"],
            "PHOTO_PENDING_OPTION_CONFLICT",
        ),
    ],
)
def test_pending_rejects_invalid_options(
    monkeypatch, fake_runtime, flags, error
):
    monkeypatch.setattr(
        sys,
        "argv",
        ["rebom_auto_process_photo_manifests.py", *flags],
    )

    with pytest.raises(RuntimeError, match=error):
        script.main()
