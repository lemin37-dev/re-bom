"""Prepare verified Silver notice and history changes without writing to Iceberg."""

from rebom.storage.silver.load_bronze import load_bronze_run
from rebom.storage.silver.load_silver_events import load_silver_events
from rebom.storage.silver.combine_verified_run import combine_verified_run
from rebom.storage.silver.prepare_notice_delta import prepare_notice_delta
from rebom.storage.silver.reconstruct_status_snapshot import reconstruct_status_snapshot
from rebom.storage.silver.prepare_notice_status_delta import prepare_notice_status_delta


def prepare_silver_pipeline(
    bronze_manifest_path,
    event_manifest_path,
    previous_silver_rows,
    previous_observed_on,
):
    bronze = load_bronze_run(bronze_manifest_path)

    events = load_silver_events(
        event_manifest_path,
        bronze["run_id"],
    )

    verified = combine_verified_run(bronze, events)

    prepared = prepare_notice_delta(
        verified,
        previous_silver_rows,
    )

    if not prepared["ready"]:
        return {
            "ready": False,
            "run_id": bronze["run_id"],
            "stage": "NOTICE_DELTA",
            "conflicts": prepared["conflicts"],
        }

    reconstructed = reconstruct_status_snapshot(
        verified,
        previous_silver_rows,
        prepared,
        previous_observed_on,
    )

    if not reconstructed["ready"]:
        return {
            "ready": False,
            "run_id": bronze["run_id"],
            "stage": "STATUS_HISTORY",
            "conflicts": reconstructed["conflicts"],
        }

    notice = prepare_notice_status_delta(
        previous_silver_rows,
        prepared,
        reconstructed,
    )

    return {
        "ready": True,
        "run_id": bronze["run_id"],
        "notice_delta_rows": notice["delta_rows"],
        "history_rows": reconstructed["history_rows"],
        "actual_closed_ids": notice["actual_closed_ids"],
        "inferred_closed_ids": notice["inferred_closed_ids"],
        "lookup_deferred_ids": verified["lookup_deferred_ids"],
        "event_conflicts": verified["event_conflicts"],
        "iceberg_write": False,
    }
