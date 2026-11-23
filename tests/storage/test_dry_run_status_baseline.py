from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from rebom.storage.silver.dry_run_baseline import build_dry_run_baseline
from rebom.storage.silver.dry_run_previous_bronze import fetch_previous_day_bronze
from rebom.storage.silver.validate_closure_evidence import validate_closure_evidence
from scripts.storage import rebom_run_aws_real_silver_test as cli
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
        "happenDt": "20261118",
    }


def test_baseline_adds_missing_confirmed_notice():
    existing = [{"desertion_no": "existing", "status_group": "PROTECTING"}]
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


NOTICE = "443447202600753"
RUN = "20261122T030013-e4e83a"


@pytest.fixture
def closed_notice():
    previous = {
        "desertion_no": NOTICE, "status_group": "CLOSED", "happen_date": date(2026, 11, 18),
        "process_state_raw": "\uc885\ub8cc(\uc790\uc5f0\uc0ac)", "special_mark": "keep",
    }
    raw = {"desertionNo": NOTICE, "happenDt": "20261118", "processState": "\ubcf4\ud638\uc911"}
    bronze = {"run_id": "20261121T030011-6f2d1f", "rows_by_kind": {"list_notice": [raw]}}
    vanished = dict(event(NOTICE), processState=previous["process_state_raw"])
    return previous, bronze, raw, vanished


@pytest.mark.parametrize("apply", [False, True])
def test_cli_retries_nonprotecting_previous_notice_only_in_dry_run(
    tmp_path, monkeypatch, capsys, closed_notice, apply,
):
    previous, bronze, _, vanished = closed_notice
    verified = {
        "rows_by_kind": {"events": [vanished]},
        "partitions": [
            {"kind": kind, "start_date": "2026-11-18", "end_date": "2026-11-22",
             "result_code": "00", "total_count": 1, "received_count": 1,
             "complete": True, "reason": "complete"}
            for kind in ("list_notice", "list_protect")
        ],
    }
    bronze_path, event_path = tmp_path / "bronze.json", tmp_path / "events.json"
    bronze_path.write_text('{"run_id": "' + RUN + '"}', encoding="utf-8")
    event_path.write_text("{}", encoding="utf-8")
    s3, notice, history = Mock(), Mock(), Mock()
    notice.scan.return_value.to_arrow.return_value.to_pylist.return_value = [previous]
    monkeypatch.setattr(cli.boto3, "client", Mock(return_value=s3))
    monkeypatch.setattr(cli, "get_tables", Mock(return_value=(Mock(), notice, history)))
    monkeypatch.setattr(cli, "restore_snapshot", Mock(return_value=False))
    monkeypatch.setattr(cli, "assert_schema", Mock())
    fetch = Mock(return_value=bronze)
    monkeypatch.setattr("rebom.storage.silver.dry_run_previous_bronze.fetch_previous_day_bronze", fetch)
    monkeypatch.setattr("rebom.storage.silver.load_silver_events.load_silver_events", Mock(return_value=verified))

    def prepare(*args, apply, dry_run_baseline_rows=None):
        rows = [previous] if dry_run_baseline_rows is None else dry_run_baseline_rows
        result = validate_closure_evidence(verified, rows)
        if not result["ready"]:
            raise RuntimeError(f"Silver preparation failed: STATUS_HISTORY; {result['rejected']}")
        assert apply is False
        assert rows[0]["status_group"] == "PROTECTING"
        assert rows[0]["process_state_raw"] == "\ubcf4\ud638\uc911"
        assert rows[0]["special_mark"] == "keep"
        return {"run_id": RUN}, SimpleNamespace(num_rows=1), SimpleNamespace(num_rows=1)

    monkeypatch.setattr(cli, "prepare_new", prepare)
    args = SimpleNamespace(
        bronze_manifest=bronze_path, event_manifest=event_path, environment="test",
        apply=apply, journal_dir=tmp_path / "journal",
        s3_state_bucket="mock-bucket", s3_state_prefix="mock-prefix",
    )
    if apply:
        with pytest.raises(RuntimeError, match="previous_not_protecting"):
            cli.execute(args)
        fetch.assert_not_called()
    else:
        cli.execute(args)
        fetch.assert_called_once()
        output = capsys.readouterr().out
        assert f"desertion_no={NOTICE} silver_status=CLOSED bronze_status=PROTECTING" in output
        assert "DRY_RUN_BASELINE_RECOVERY: PASS" in output
    assert previous["status_group"] == "CLOSED"
    s3.put_object.assert_not_called()
    notice.upsert.assert_not_called()
    history.upsert.assert_not_called()


@pytest.mark.parametrize("problem, error", [
    ("silver_date", "BASELINE_HAPPEN_DATE_MISMATCH"),
    ("event_date", "BASELINE_HAPPEN_DATE_MISMATCH"),
    ("wrong_notice", "DRY_RUN_PREVIOUS_BRONZE_MISSING"),
    ("duplicate_status", "BASELINE_CONFLICT"),
    ("duplicate_date", "BASELINE_CONFLICT"),
    ("duplicate_silver", "Silver"),
    ("duplicate_event", "BASELINE_EVENT_ID_CONFLICT"),
])
def test_baseline_rejects_conflicting_identity_and_status(closed_notice, problem, error):
    previous, bronze, raw, vanished = closed_notice
    rows, events = [previous], [vanished]
    if problem == "silver_date":
        previous["happen_date"] = date(2026, 11, 19)
    elif problem == "event_date":
        vanished["happenDt"] = "20261119"
    elif problem == "wrong_notice":
        raw["desertionNo"] = "other"
    elif problem == "duplicate_status":
        bronze["rows_by_kind"]["list_protect"] = [dict(raw, processState=vanished["processState"])]
    elif problem == "duplicate_date":
        bronze["rows_by_kind"]["list_protect"] = [dict(raw, happenDt="20261119")]
    elif problem == "duplicate_silver":
        rows.append(dict(previous))
    elif problem == "duplicate_event":
        events.append(dict(vanished))
    with pytest.raises(ValueError, match=error):
        build_dry_run_baseline(rows, bronze, events)


def test_previous_bronze_rejects_run_date_outside_requested_partition(tmp_path):
    s3 = Mock()
    s3.get_paginator.return_value.paginate.return_value = [{
        "Contents": [{"Key": PREVIOUS_KEY.replace(PREVIOUS, CURRENT)}]
    }]
    with pytest.raises(RuntimeError, match="PREVIOUS_BRONZE_RUN_DATE_MISMATCH"):
        fetch_previous_day_bronze(s3, "bucket", CURRENT, tmp_path)
    s3.download_file.assert_not_called()
