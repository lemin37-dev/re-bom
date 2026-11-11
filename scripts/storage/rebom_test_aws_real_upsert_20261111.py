"""서울 Iceberg UPDATE 및 상태 이력 테스트."""

from copy import deepcopy

import pyarrow as pa

from rebom.storage.silver.aws_catalog import get_aws_catalog

DB = "rebom_silver"
BUCKET = "rebom-data-559987919299"
PREFIX = f"s3://{BUCKET}/bench_tmp/b-storage-test/iceberg/real-bronze-20261111"

NOTICE_SOURCE = f"{DB}.silver_animal_notice_real_20261111_test"
HISTORY_SOURCE = f"{DB}.silver_animal_status_history_real_20261111_test"

NOTICE_TEST = f"{DB}.silver_animal_notice_update_real_20261111"
HISTORY_TEST = f"{DB}.silver_animal_history_update_real_20261111"

TEST_ID = "B-REAL-UPSERT-TEST-20261111"
HISTORY_KEYS = ["run_id", "desertion_no", "observed_on", "event_type"]

catalog = get_aws_catalog()

for name in (NOTICE_TEST, HISTORY_TEST):
    if catalog.table_exists(name):
        raise RuntimeError(f"Test table already exists: {name}")

notice_source = catalog.load_table(NOTICE_SOURCE)
history_source = catalog.load_table(HISTORY_SOURCE)

notice_rows = notice_source.scan().to_arrow().to_pylist()
history_rows = history_source.scan().to_arrow().to_pylist()

sample = next(
    (row for row in notice_rows if row["status_group"] == "PROTECTING"),
    None,
)

if sample is None:
    raise RuntimeError("No protecting notice found")

sample_history = next(
    (
        row for row in history_rows
        if row["desertion_no"] == sample["desertion_no"]
        and row["event_type"] == "INITIAL"
    ),
    None,
)

if sample_history is None:
    raise RuntimeError("Matching initial history not found")

notice = deepcopy(sample)
notice["desertion_no"] = TEST_ID

initial_history = deepcopy(sample_history)
initial_history["desertion_no"] = TEST_ID

changed_notice = deepcopy(notice)
changed_notice["process_state_raw"] = "종료(입양)"
changed_notice["status_group"] = "CLOSED"

changed_history = deepcopy(initial_history)
changed_history.update({
    "run_id": "20261111T160000-b00001",
    "fetched_at": "2026-11-11T16:00:00+09:00",
    "observed_on": "2026-11-11",
    "event_type": "CHANGED",
    "previous_state_raw": initial_history["process_state_raw"],
    "process_state_raw": "종료(입양)",
    "status_group": "CLOSED",
    "confirmed_by": "manual_test",
    "is_inferred": False,
})

catalog.create_table(
    NOTICE_TEST,
    schema=notice_source.schema(),
    location=f"{PREFIX}/silver_animal_notice_update_real_20261111",
)

catalog.create_table(
    HISTORY_TEST,
    schema=history_source.schema(),
    location=f"{PREFIX}/silver_animal_history_update_real_20261111",
)

nt = catalog.load_table(NOTICE_TEST)
ht = catalog.load_table(HISTORY_TEST)

def upsert(table, rows, keys):
    arrow = pa.Table.from_pylist(
        rows,
        schema=table.schema().as_arrow(),
    )
    return table.upsert(
        arrow,
        join_cols=keys,
        when_matched_update_all=True,
        when_not_matched_insert_all=True,
    )

def check():
    nr = catalog.load_table(NOTICE_TEST).scan().to_arrow().to_pylist()
    hr = catalog.load_table(HISTORY_TEST).scan().to_arrow().to_pylist()
    return nr, hr

r = upsert(nt, [notice], ["desertion_no"])
print("INITIAL_NOTICE_INSERTED:", r.rows_inserted)

r = upsert(ht, [initial_history], HISTORY_KEYS)
print("INITIAL_HISTORY_INSERTED:", r.rows_inserted)

r = upsert(nt, [changed_notice], ["desertion_no"])
print("CHANGED_NOTICE_UPDATED:", r.rows_updated)

r = upsert(ht, [changed_history], HISTORY_KEYS)
print("CHANGED_HISTORY_INSERTED:", r.rows_inserted)

nr, hr = check()

assert len(nr) == 1, f"Notice count: {len(nr)}"
assert len(hr) == 2, f"History count: {len(hr)}"
assert nr[0]["status_group"] == "CLOSED"
assert nr[0]["process_state_raw"] == "종료(입양)"
assert {r["event_type"] for r in hr} == {"INITIAL", "CHANGED"}

print("UPDATE_VALIDATION: PASS")

upsert(nt, [changed_notice], ["desertion_no"])
upsert(ht, [changed_history], HISTORY_KEYS)

nr, hr = check()

assert len(nr) == 1, f"Notice count after retry: {len(nr)}"
assert len(hr) == 2, f"History count after retry: {len(hr)}"
assert nr[0]["status_group"] == "CLOSED"

print("NOTICE_TOTAL:", len(nr))
print("HISTORY_TOTAL:", len(hr))
print("RETRY_VALIDATION: PASS")
