"""Combine verified actual and inferred closure changes without writing Iceberg."""

from rebom.storage.silver.prepare_actual_closed_delta import (
    prepare_actual_closed_delta,
)
from rebom.storage.silver.prepare_notice_closure import (
    prepare_notice_closure,
)
from rebom.storage.silver.reconstruct_status_snapshot import index_silver


def prepare_notice_status_delta(previous_rows, prepared_notice, reconstructed):
    if prepared_notice.get("ready") is not True:
        raise ValueError("NOTICE_DELTA_NOT_READY")

    if reconstructed.get("ready") is not True:
        raise ValueError("STATUS_HISTORY_NOT_READY")

    actual = prepare_actual_closed_delta(
        previous_rows, prepared_notice, reconstructed
    )
    inferred = prepare_notice_closure(
        previous_rows, reconstructed
    )

    rows = index_silver(actual["delta_rows"])
    actual_ids = set(actual["actual_closed_ids"])
    inferred_ids = set(inferred["closure_ids"])

    if actual_ids & inferred_ids:
        raise ValueError("ACTUAL_INFERRED_CLOSURE_OVERLAP")

    for row in inferred["delta_rows"]:
        key = row["desertion_no"]

        if key in rows:
            raise ValueError(f"INFERRED_NOTICE_DELTA_CONFLICT: {key}")

        rows[key] = row

    return {
        "ready": True,
        "delta_rows": list(rows.values()),
        "actual_closed_ids": sorted(actual_ids),
        "inferred_closed_ids": sorted(inferred_ids),
        "delta_count": len(rows),
    }
