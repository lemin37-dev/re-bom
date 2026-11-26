"""서울 AWS Silver 보호소 테스트 적재."""

import os
from collections import Counter

import pyarrow as pa

from rebom.storage.silver.aws_catalog import get_aws_catalog
from rebom.storage.silver.load_bronze import load_bronze_run
from rebom.storage.silver.transform_shelter import transform_shelters

TABLE_NAME = "rebom_silver.silver_shelter_b_test"
EXPECTED_LOCATION = (
    "s3://rebom-data-559987919299/"
    "bench_tmp/b-storage-test/iceberg/silver_shelter_b_test"
)

bronze = load_bronze_run(os.environ["REBOM_BRONZE_MANIFEST"])

rows = [
    row
    for kind in ("updated", "list_notice", "list_protect")
    for row in bronze["rows_by_kind"][kind]
]

shelters, conflicts, missing = transform_shelters(rows)

if missing:
    raise RuntimeError(f"Missing shelter keys: {len(missing)}")

conflict_fields = {
    (item["care_reg_no"], item["field"])
    for item in conflicts
}

if any(field not in {"care_tel", "org_name"} for _, field in conflict_fields):
    raise RuntimeError("Unexpected shelter conflict field")

for shelter in shelters:
    for field in ("care_tel", "org_name"):
        if (shelter["care_reg_no"], field) in conflict_fields:
            shelter[field] = None

keys = [item["care_reg_no"] for item in shelters]

if len(keys) != len(set(keys)):
    raise RuntimeError("Duplicate shelter keys")

print("SHELTER_PREPARED:", len(shelters))
print("CONFLICT_RECORDS:", len(conflicts))
print("CONFLICT_FIELDS:", dict(Counter(field for _, field in conflict_fields)))
print("CONFLICT_VALUES_WITHHELD:", len(conflict_fields))

catalog = get_aws_catalog()
table = catalog.load_table(TABLE_NAME)

if table.location().rstrip("/") != EXPECTED_LOCATION:
    raise RuntimeError("Unexpected Iceberg location")

before = table.scan().to_arrow().num_rows
print("BEFORE:", before)

if before != 0:
    raise RuntimeError("Shelter test table is not empty")

arrow = pa.Table.from_pylist(
    shelters,
    schema=table.schema().as_arrow(),
)

table.append(arrow)

stored = catalog.load_table(TABLE_NAME).scan().to_arrow().to_pylist()
stored_by_key = {row["care_reg_no"]: row for row in stored}

if len(stored) != len(shelters) or len(stored_by_key) != len(shelters):
    raise RuntimeError("Shelter count or key mismatch")

if any(stored_by_key[row["care_reg_no"]] != row for row in shelters):
    raise RuntimeError("Shelter data mismatch")

print("SHELTER_TOTAL:", len(stored))
print("SHELTER_ICEBERG_WRITE: PASS")
