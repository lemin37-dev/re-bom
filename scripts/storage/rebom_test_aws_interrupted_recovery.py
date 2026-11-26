"""Verify an interrupted Silver notice/history UPSERT across processes."""

import argparse
from copy import deepcopy

import pyarrow as pa

from rebom.storage.silver.aws_catalog import get_aws_catalog


DB = "rebom_silver"
NOTICE_SOURCE = f"{DB}.silver_animal_notice_real_20261111_test"
HISTORY_SOURCE = f"{DB}.silver_animal_status_history_real_20261111_test"
NOTICE_TEST = f"{DB}.silver_animal_notice_update_real_20261111"
HISTORY_TEST = f"{DB}.silver_animal_history_update_real_20261111"

TEST_ID = "B-INTERRUPTED-RECOVERY-20261111"
RUN_ID = "20261111T170000-b00002"
HISTORY_KEYS = ["run_id", "desertion_no", "observed_on", "event_type"]

parser = argparse.ArgumentParser()
parser.add_argument("--phase", choices=["interrupt", "recover"], required=True)
args = parser.parse_args()

catalog = get_aws_catalog()

for name in (NOTICE_TEST, HISTORY_TEST):
    if not catalog.table_exists(name):
        raise RuntimeError(f"Existing isolated test table missing: {name}")

nt = catalog.load_table(NOTICE_TEST)
ht = catalog.load_table(HISTORY_TEST)

def rows(table, test_id=TEST_ID):
    return [
        row for row in catalog.load_table(table.name()).scan().to_arrow().to_pylist()
        if row["desertion_no"] == test_id
    ]

def write(table, values, keys):
    arrow = pa.Table.from_pylist(
        values,
        schema=table.schema().as_arrow(),
    )
    return table.upsert(
        arrow,
        join_cols=keys,
        when_matched_update_all=True,
        when_not_matched_insert_all=True,
    )

def get_changed_history(initial):
    changed = deepcopy(initial)
    changed.update({
        "run_id": RUN_ID,
        "fetched_at": "2026-11-11T17:00:00+09:00",
        "observed_on": "2026-11-11",
        "event_type": "CHANGED",
        "previous_state_raw": initial["process_state_raw"],
        "process_state_raw": "??(??)",
        "status_group": "CLOSED",
        "confirmed_by": "manual_test",
        "is_inferred": False,
    })
    return changed

if args.phase == "interrupt":
    if rows(nt) or rows(ht):
        raise RuntimeError("Test ID already exists; refusing to overwrite")

    source_notice = next(
        (r for r in catalog.load_table(NOTICE_SOURCE).scan().to_arrow().to_pylist()
         if r["status_group"] == "PROTECTING"),
        None,
    )
    if source_notice is None:
        raise RuntimeError("No PROTECTING sample")

    source_history = next(
        (r for r in catalog.load_table(HISTORY_SOURCE).scan().to_arrow().to_pylist()
         if r["desertion_no"] == source_notice["desertion_no"]
         and r["event_type"] == "INITIAL"),
        None,
    )
    if source_history is None:
        raise RuntimeError("Matching INITIAL history missing")

    notice = deepcopy(source_notice)
    notice["desertion_no"] = TEST_ID

    initial = deepcopy(source_history)
    initial["desertion_no"] = TEST_ID

    write(nt, [notice], ["desertion_no"])
    write(ht, [initial], HISTORY_KEYS)

    changed_notice = deepcopy(notice)
    changed_notice["process_state_raw"] = "??(??)"
    changed_notice["status_group"] = "CLOSED"

    write(nt, [changed_notice], ["desertion_no"])

    actual_notice = rows(nt)
    actual_history = rows(ht)

    assert len(actual_notice) == 1
    assert actual_notice[0]["status_group"] == "CLOSED"
    assert len(actual_history) == 1
    assert actual_history[0]["event_type"] == "INITIAL"

    print("NOTICE_UPDATED: PASS")
    print("HISTORY_NOT_YET_WRITTEN: PASS")
    print("SIMULATED_INTERRUPTION: PASS")

else:
    actual_notice = rows(nt)
    actual_history = rows(ht)

    assert len(actual_notice) == 1
    assert actual_notice[0]["status_group"] == "CLOSED"

    initial_rows = [
        r for r in actual_history if r["event_type"] == "INITIAL"
    ]
    assert len(initial_rows) == 1

    changed = get_changed_history(initial_rows[0])

    existing_changed = [
        r for r in actual_history
        if tuple(r.get(k) for k in HISTORY_KEYS)
        == tuple(changed.get(k) for k in HISTORY_KEYS)
    ]

    if existing_changed and existing_changed[0] != changed:
        raise RuntimeError("Existing CHANGED history differs")

    if not existing_changed:
        write(ht, [changed], HISTORY_KEYS)

    recovered = rows(ht)
    assert len(recovered) == 2
    assert {r["event_type"] for r in recovered} == {"INITIAL", "CHANGED"}
    assert sum(
        1 for r in recovered
        if tuple(r.get(k) for k in HISTORY_KEYS)
        == tuple(changed.get(k) for k in HISTORY_KEYS)
    ) == 1

    print("NOTICE_TOTAL: 1")
    print("HISTORY_TOTAL: 2")
    print("HISTORY_RECOVERY: PASS")
    print("INTERRUPTED_UPSERT_RECOVERY: PASS")
