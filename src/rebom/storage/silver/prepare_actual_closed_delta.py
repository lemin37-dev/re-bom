"""Apply verified actual closures to prepared Silver notice rows."""

from rebom.storage.cleaning.status import parse_status
from rebom.storage.silver.reconstruct_status_snapshot import index_silver


def prepare_actual_closed_delta(previous_rows, prepared_notice, reconstructed):
    if prepared_notice.get("ready") is not True:
        raise ValueError("NOTICE_DELTA_NOT_READY")

    if reconstructed.get("ready") is not True:
        raise ValueError("STATUS_HISTORY_NOT_READY")

    previous = index_silver(previous_rows)
    delta = index_silver(prepared_notice.get("delta_rows") or [])

    resolved = reconstructed.get("resolved") or {}
    closed_ids = set(resolved.get("actual_closed_ids") or [])

    history = reconstructed.get("history_rows") or []

    for key in sorted(closed_ids):
        old = previous.get(key)

        if old is None:
            raise ValueError(f"PREVIOUS_NOTICE_MISSING: {key}")

        matches = [
            row for row in history
            if row.get("desertion_no") == key
            and row.get("event_type") == "CHANGED"
            and row.get("is_inferred") is False
            and row.get("status_group") == "CLOSED"
        ]

        if len(matches) != 1:
            raise ValueError(f"VERIFIED_CLOSURE_HISTORY_MISSING: {key}")

        history_row = matches[0]
        state = history_row["process_state_raw"]
        group, reason = parse_status(state)

        if group != "CLOSED":
            raise ValueError(f"INVALID_CLOSURE_STATE: {key}")

        old_group, _ = parse_status(old.get("process_state_raw"))

        if old_group not in ("PROTECTING", "CLOSED"):
            raise ValueError(f"INVALID_PREVIOUS_STATE: {key}")

        new = dict(delta.get(key, old))
        new["process_state_raw"] = state
        new["status_group"] = group
        new["closure_reason_code"] = reason
        new["source_updated_at_raw"] = history_row.get(
            "source_updated_at_raw"
        )

        if new != old:
            delta[key] = new
        else:
            delta.pop(key, None)

    return {
        "ready": True,
        "delta_rows": list(delta.values()),
        "actual_closed_ids": sorted(closed_ids),
    }
