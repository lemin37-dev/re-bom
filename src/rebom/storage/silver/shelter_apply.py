"""??? Iceberg ?? ?? ? ?? ?? ??."""

import re
from datetime import datetime

from rebom.storage.silver.apply_silver_pipeline import prepare_arrow

SHELTER_TABLE = "rebom_silver.silver_shelter_b_test"

SHELTER_LOCATION = (
    "s3://rebom-data-559987919299/"
    "bench_tmp/b-storage-test/iceberg/silver_shelter_b_test"
)

SHELTER_KEYS = ("care_reg_no",)
BOOTSTRAP_RUN_ID = "20261124T030012-f65718"


def run_time(run_id):
    if not isinstance(run_id, str) or not re.fullmatch(
        r"[0-9]{8}T[0-9]{6}-[a-f0-9]{6}", run_id
    ):
        raise ValueError("INVALID_SHELTER_RUN_ID")

    return datetime.strptime(run_id[:15], "%Y%m%dT%H%M%S")


def check_run_order(run_id, previous_run_id):
    current = run_time(run_id)
    previous = run_time(previous_run_id)

    if run_id == previous_run_id:
        return "RETRY"

    if current < previous:
        raise RuntimeError("STALE_SHELTER_RUN")

    if current == previous:
        raise RuntimeError("SHELTER_RUN_TIME_CONFLICT")

    return "ALLOWED"


def validate_shelter_table(table):
    if ".".join(table.name()) != SHELTER_TABLE:
        raise RuntimeError("SHELTER_TABLE_NAME_MISMATCH")

    if table.location().rstrip("/") != SHELTER_LOCATION:
        raise RuntimeError("SHELTER_TABLE_LOCATION_MISMATCH")


def apply_shelter_delta(table, delta_rows):
    validate_shelter_table(table)

    if not delta_rows:
        return {
            "inserted": 0,
            "updated": 0,
            "iceberg_write": False,
        }

    arrow = prepare_arrow(
        delta_rows,
        table,
        SHELTER_KEYS,
        require_all_fields=True,
    )

    result = table.upsert(
        arrow,
        join_cols=list(SHELTER_KEYS),
        when_matched_update_all=True,
        when_not_matched_insert_all=True,
    )

    table.refresh()

    stored = {
        row["care_reg_no"]: row
        for row in table.scan().to_arrow().to_pylist()
    }

    for row in delta_rows:
        if stored.get(row["care_reg_no"]) != row:
            raise RuntimeError("SHELTER_WRITE_VERIFICATION_FAILED")

    return {
        "inserted": result.rows_inserted,
        "updated": result.rows_updated,
        "iceberg_write": bool(
            result.rows_inserted or result.rows_updated
        ),
    }
