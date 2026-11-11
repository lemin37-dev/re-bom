import pyarrow as pa

from rebom.storage.silver.apply_notice_pipeline import (
    get_local_catalog,
    SOURCE_TABLE,
)

catalog = get_local_catalog()
source = catalog.load_table(SOURCE_TABLE)

test_table_name = "rebom_silver.silver_notice_closure_verify_test"

if catalog.table_exists(test_table_name):
    raise RuntimeError("TEST_TABLE_ALREADY_EXISTS")

source_rows = source.scan().to_arrow().to_pylist()
if not source_rows:
    raise RuntimeError("SOURCE_TABLE_EMPTY")

original = dict(source_rows[0])
key = original["desertion_no"]

protecting = "\ubcf4\ud638\uc911"
closed = "\uc885\ub8cc(\uc785\uc591)"
adoption = "\uc785\uc591"

schema = source.schema().as_arrow()

before = dict(original)
before["process_state_raw"] = protecting
before["status_group"] = "PROTECTING"
before["closure_reason_code"] = None

after = dict(before)
after["process_state_raw"] = closed
after["status_group"] = "CLOSED"
after["closure_reason_code"] = adoption

table = None

try:
    table = catalog.create_table(
        identifier=test_table_name,
        schema=source.schema(),
    )

    table.upsert(
        pa.Table.from_pylist([before], schema=schema),
        join_cols=["desertion_no"],
    )

    stored_before = catalog.load_table(
        test_table_name
    ).scan().to_arrow().to_pylist()

    assert len(stored_before) == 1
    assert stored_before[0]["process_state_raw"] == protecting
    print("INITIAL_PROTECTING_WRITE: PASS")

    table = catalog.load_table(test_table_name)
    table.upsert(
        pa.Table.from_pylist([after], schema=schema),
        join_cols=["desertion_no"],
    )

    stored = catalog.load_table(
        test_table_name
    ).scan().to_arrow().to_pylist()

    assert len(stored) == 1, stored
    assert stored[0]["desertion_no"] == key
    assert stored[0]["process_state_raw"] == closed
    assert stored[0]["status_group"] == "CLOSED"
    assert stored[0]["closure_reason_code"] == adoption
    assert stored[0] == after

    print("ICEBERG_UPSERT: PASS")
    print("ADOPTED_STATUS_READBACK: PASS")
    print("OTHER_FIELDS_PRESERVED: PASS")
    print("ROW_COUNT_UNCHANGED: PASS")
    print("RESULT: PASS")

finally:
    if table is not None:
        catalog.drop_table(test_table_name)
        print("TEMP_TEST_TABLE_REMOVED: PASS")
