import sys
from unittest.mock import patch

import pytest

from scripts.storage import rebom_recover_missing_photos as recovery


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["recovery"], False),
        (["recovery", "--apply"], True),
    ],
)
def test_apply_option(argv, expected):
    with patch.object(sys, "argv", argv):
        with patch.object(recovery.boto3, "Session") as session:
            session.return_value.client.return_value.get_caller_identity.return_value = {
                "Account": "invalid"
            }

            with pytest.raises(RuntimeError, match="UNEXPECTED_AWS_ACCOUNT"):
                recovery.main()

            session.return_value.client.assert_called_once_with("sts")


def test_recovery_target():
    assert recovery.TARGET == "446482202500677"
    assert recovery.PHOTO_TABLE.endswith("_test")
    assert recovery.HISTORY_TABLE.endswith("_test")
    assert "bench_tmp/b-storage-test/" in recovery.PHOTO_LOCATION


@pytest.mark.parametrize(
    "plan",
    [
        {"insert_count": 2, "update_count": 1, "held_slots": []},
        {"insert_count": 1, "update_count": 0, "held_slots": []},
        {"insert_count": 2, "update_count": 0, "held_slots": [1]},
    ],
)
def test_unsafe_plan_blocked(plan):
    with patch.object(sys, "argv", ["recovery", "--apply"]):
        with patch.object(recovery.boto3, "Session") as session:
            client = session.return_value.client.return_value
            client.get_caller_identity.return_value = {
                "Account": recovery.ACCOUNT
            }
            client.head_object.return_value = {
                "Metadata": {"sha256": "test-hash"}
            }

            with patch.object(recovery, "get_aws_catalog") as get_catalog:
                table = get_catalog.return_value.load_table.return_value
                table.scan.return_value.to_arrow.return_value.to_pylist.side_effect = [
                    [{"status_group": "PROTECTING"}],
                    [
                        {
                            "image_slot": slot,
                            "version": 1,
                            "status": "stored",
                            "run_id": recovery.SOURCE_RUN,
                            "sha256": "test-hash",
                            "original_s3_key": f"original/{slot}",
                            "serving_s3_key": f"serving/{slot}",
                            "thumb_s3_key": f"thumb/{slot}",
                        }
                        for slot in (1, 2)
                    ],
                ]

                with patch.object(
                    recovery,
                    "apply_photo_current_updates",
                    return_value=plan,
                ) as apply_updates:
                    with pytest.raises(
                        RuntimeError,
                        match="UNSAFE_RECOVERY_PLAN|UNEXPECTED_INSERT_COUNT",
                    ):
                        recovery.main()

                    assert apply_updates.call_count == 1
                    assert apply_updates.call_args.kwargs["apply"] is False


@pytest.mark.parametrize(
    "scenario",
    ["apply", "already_completed", "snapshot_changed"],
)
def test_recovery_execution(scenario):
    with patch.object(sys, "argv", ["recovery", "--apply"]):
        with patch.object(recovery.boto3, "Session") as session:
            client = session.return_value.client.return_value
            client.get_caller_identity.return_value = {
                "Account": recovery.ACCOUNT
            }
            client.head_object.return_value = {
                "Metadata": {"sha256": "test-hash"}
            }

            with patch.object(recovery, "get_aws_catalog") as get_catalog:
                table = get_catalog.return_value.load_table.return_value
                table.scan.return_value.to_arrow.return_value.to_pylist.side_effect = [
                    [{"status_group": "PROTECTING"}],
                    [
                        {
                            "image_slot": slot,
                            "version": 1,
                            "status": "stored",
                            "run_id": recovery.SOURCE_RUN,
                            "sha256": "test-hash",
                            "original_s3_key": f"original/{slot}",
                            "serving_s3_key": f"serving/{slot}",
                            "thumb_s3_key": f"thumb/{slot}",
                        }
                        for slot in (1, 2)
                    ],
                ]

                from types import SimpleNamespace

                table.current_snapshot.side_effect = [
                    SimpleNamespace(snapshot_id=100),
                    SimpleNamespace(
                        snapshot_id=101 if scenario == "snapshot_changed" else 100
                    ),
                    SimpleNamespace(snapshot_id=102),
                ]

                plan = {
                    "insert_count": 0 if scenario == "already_completed" else 2,
                    "update_count": 0,
                    "held_slots": [],
                }

                applied = {
                    "insert_count": 2,
                    "update_count": 0,
                    "result": "APPLIED_AND_VERIFIED",
                }

                with patch.object(
                    recovery,
                    "apply_photo_current_updates",
                    side_effect=[plan, applied],
                ) as updates:
                    if scenario == "snapshot_changed":
                        with pytest.raises(RuntimeError, match="SNAPSHOT_CHANGED"):
                            recovery.main()
                    else:
                        recovery.main()

                    if scenario == "apply":
                        assert updates.call_count == 2
                        assert updates.call_args.kwargs["apply"] is True
                    else:
                        assert updates.call_count == 1
                        assert updates.call_args.kwargs["apply"] is False
