"""서울 Glue Iceberg 테스트 적재."""

import os

from rebom.storage.silver.aws_catalog import get_aws_catalog
from rebom.storage.silver.prepare_silver_pipeline import prepare_silver_pipeline
from rebom.storage.silver.apply_silver_pipeline import prepare_arrow

NOTICE = "rebom_silver.silver_animal_notice_b_test"
HISTORY = "rebom_silver.silver_animal_status_history_b_test"
EXPECTED_PREFIX = "s3://rebom-data-559987919299/bench_tmp/b-storage-test/iceberg/"

catalog = get_aws_catalog()

notice_table = catalog.load_table(NOTICE)
history_table = catalog.load_table(HISTORY)

for table in (notice_table, history_table):
    location = table.location().rstrip("/") + "/"
    if not location.startswith(EXPECTED_PREFIX):
        raise RuntimeError(f"Unexpected table location: {location}")

    count = table.scan().to_arrow().num_rows
    print(f"BEFORE: {table.name()} = {count}")

    if count != 0:
        raise RuntimeError("Test table is not empty. Stop to avoid unintended changes.")

prepared = prepare_silver_pipeline(
    bronze_manifest_path=os.environ["REBOM_BRONZE_MANIFEST"],
    event_manifest_path=os.environ["REBOM_EVENT_MANIFEST"],
    previous_silver_rows=[],
    previous_observed_on=None,
)

if not prepared["ready"]:
    raise RuntimeError(f"Silver preparation failed: {prepared['stage']}")

notice_arrow = prepare_arrow(
    prepared["notice_delta_rows"],
    notice_table,
    ("desertion_no",),
    require_all_fields=True,
)

history_arrow = prepare_arrow(
    prepared["history_rows"],
    history_table,
    ("run_id", "desertion_no", "observed_on", "event_type"),
    require_all_fields=True,
)

print("PREPARED_NOTICE:", notice_arrow.num_rows)
print("PREPARED_HISTORY:", history_arrow.num_rows)

notice_result = notice_table.upsert(
    notice_arrow,
    join_cols=["desertion_no"],
    when_matched_update_all=True,
    when_not_matched_insert_all=True,
)

print("NOTICE_INSERTED:", notice_result.rows_inserted)
print("NOTICE_UPDATED:", notice_result.rows_updated)

history_result = history_table.upsert(
    history_arrow,
    join_cols=["run_id", "desertion_no", "observed_on", "event_type"],
    when_matched_update_all=True,
    when_not_matched_insert_all=True,
)

print("HISTORY_INSERTED:", history_result.rows_inserted)
print("HISTORY_UPDATED:", history_result.rows_updated)

notice_count = catalog.load_table(NOTICE).scan().to_arrow().num_rows
history_count = catalog.load_table(HISTORY).scan().to_arrow().num_rows

print("NOTICE_TOTAL:", notice_count)
print("HISTORY_TOTAL:", history_count)

if notice_count != 19740 or history_count != 19740:
    raise RuntimeError("Iceberg row count verification failed")

print("AWS_ICEBERG_WRITE: PASS")
