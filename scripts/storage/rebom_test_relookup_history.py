from rebom.storage.silver.reconstruct_status_snapshot import reconstruct_status_snapshot
from rebom.storage.silver.validate_manifest import validate_partition

protecting = "\ubcf4\ud638\uc911"
closed = "\uc885\ub8cc(\uc785\uc591)"

previous = [{
    "desertion_no": "A001",
    "happen_date": "2026-11-01",
    "process_state_raw": protecting,
    "status_group": "PROTECTING",
    "source_updated_at_raw": None,
}]

event = {
    "event": "vanished",
    "desertionNo": "A001",
    "happenDt": "20261101",
    "confirmed_by": "complete_range",
    "state_source": "relookup",
    "processState": closed,
}

lookup = {
    "desertionNo": "A001",
    "happenDt": "20261101",
    "processState": closed,
}

partitions = []
for kind in ("list_notice", "list_protect"):
    complete, reason = validate_partition(
        "00", "2026-11-01", "2026-11-01", 1, 1
    )
    partitions.append({
        "kind": kind,
        "result_code": "00",
        "start_date": "2026-11-01",
        "end_date": "2026-11-01",
        "total_count": 1,
        "received_count": 1,
        "complete": complete,
        "reason": reason,
    })

run = {
    "run_id": "20261103T090045-1fe465",
    "fetched_at": "2026-11-03T09:00:45+09:00",
    "partitions": partitions,
    "rows_by_kind": {
        "updated": [{
            "desertionNo": "A001",
            "processState": protecting,
        }],
        "list_notice": [],
        "list_protect": [],
        "events": [event],
        "lookup": [lookup],
    },
    "reprocess_ids": [],
    "lookup_deferred_ids": [],
}

prepared = {"ready": True, "delta_rows": []}

result = reconstruct_status_snapshot(
    verified_run=run,
    previous_silver_rows=previous,
    prepared_notice=prepared,
    previous_observed_on="2026-11-02",
)

assert result["ready"], result

changes = [
    row for row in result["history_rows"]
    if row["event_type"] == "CHANGED"
]

assert len(changes) == 1, changes
assert changes[0]["process_state_raw"] == closed, changes
assert changes[0]["status_group"] == "CLOSED", changes
assert changes[0]["is_inferred"] is False, changes

print("STATUS_CHANGED_TO_ADOPTED: PASS")
print("NOT_INFERRED_CLOSURE: PASS")
print("RESULT: PASS")
