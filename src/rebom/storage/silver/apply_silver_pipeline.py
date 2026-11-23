"""Apply prepared Silver changes to isolated Iceberg test tables."""

import pyarrow as pa
from pyiceberg.expressions import In
from pyiceberg.table import DataScan, UpsertResult

from rebom.storage.silver.apply_notice_pipeline import get_local_catalog


NOTICE_TABLE = "rebom_silver.silver_animal_notice_ab_integration_test"
HISTORY_TABLE = "rebom_silver.silver_animal_status_history_ab_integration_test"

NOTICE_KEYS = ("desertion_no",)
HISTORY_KEYS = ("run_id", "desertion_no", "observed_on", "event_type")


def index_rows(rows, keys):
    result = {}

    for row in rows:
        key = tuple(row.get(column) for column in keys)

        if any(value is None or value == "" for value in key):
            raise ValueError(f"Missing key: {key}")

        if key in result:
            raise ValueError(f"Duplicate key: {key}")

        result[key] = row

    return result


def prepare_arrow(rows, table, keys, require_all_fields=False):
    schema = table.schema().as_arrow()
    columns = {field.name for field in schema}

    for row in rows:
        extra = set(row) - columns
        if extra:
            raise ValueError(f"Unexpected columns: {sorted(extra)}")

        missing = [
            field.name
            for field in schema
            if field.name not in row
            and (require_all_fields or not field.nullable)
        ]
        if missing:
            raise ValueError(f"Missing columns: {missing}")

    index_rows(rows, keys)
    return pa.Table.from_pylist(rows, schema=schema)


def upsert_notice(table, arrow):
    """Upsert on main, projecting its rows onto the current schema after evolution."""
    snapshot = table.current_snapshot()
    schema = table.schema()
    if snapshot is None or snapshot.schema_id in (None, schema.schema_id):
        return table.upsert(arrow, join_cols=list(NOTICE_KEYS))

    if not arrow.schema.equals(schema.as_arrow(), check_metadata=False):
        raise ValueError("Notice upsert requires the current table schema")
    incoming = index_rows(arrow.to_pylist(), NOTICE_KEYS)
    if not incoming:
        return UpsertResult(rows_updated=0, rows_inserted=0)

    key_column, = NOTICE_KEYS
    with table.transaction() as tx:
        # An unpinned scan reads main using the current schema. Iceberg projects
        # nullable columns missing from old data files as null by field ID.
        scan = DataScan(
            table_metadata=tx.table_metadata,
            io=table.io,
            row_filter=In(key_column, [key for key, in incoming]),
        )
        existing = index_rows(scan.to_arrow().to_pylist(), NOTICE_KEYS)
        inserts = []
        updates = []
        for position, (key, row) in enumerate(incoming.items()):
            if key not in existing:
                inserts.append(position)
            elif existing[key] != row:
                updates.append(position)

        if updates:
            tx.overwrite(
                arrow.take(updates),
                overwrite_filter=In(key_column, arrow[key_column].take(updates).to_pylist()),
            )
        if inserts:
            tx.append(arrow.take(inserts))

    return UpsertResult(rows_updated=len(updates), rows_inserted=len(inserts))


def verify_rows(catalog, table_name, expected, keys):
    stored = index_rows(
        catalog.load_table(table_name).scan().to_arrow().to_pylist(),
        keys,
    )

    for key, row in expected.items():
        if key not in stored or stored[key] != row:
            raise RuntimeError(f"Iceberg verification failed: {key}")

    return len(stored)


def apply_prepared_silver(prepared, apply=False, stop_after_notice=False):
    if not prepared.get("ready"):
        raise ValueError("Silver preparation is not ready")

    if not prepared.get("run_id"):
        raise ValueError("Missing run_id")

    catalog = get_local_catalog()

    for name in (NOTICE_TABLE, HISTORY_TABLE):
        if not catalog.table_exists(name):
            raise RuntimeError(f"Missing test table: {name}")

    notice_table = catalog.load_table(NOTICE_TABLE)
    history_table = catalog.load_table(HISTORY_TABLE)

    notice_rows = prepared["notice_delta_rows"]
    history_rows = prepared["history_rows"]

    notice_arrow = prepare_arrow(
        notice_rows, notice_table, NOTICE_KEYS, require_all_fields=True
    )
    history_arrow = prepare_arrow(
        history_rows, history_table, HISTORY_KEYS
    )

    notice_expected = index_rows(
        notice_arrow.to_pylist(), NOTICE_KEYS
    )
    history_expected = index_rows(
        history_arrow.to_pylist(), HISTORY_KEYS
    )

    result = {
        "run_id": prepared["run_id"],
        "notice_prepared": len(notice_rows),
        "history_prepared": len(history_rows),
        "mode": "DRY_RUN" if not apply else "APPLY",
    }

    if not apply:
        return result

    if notice_rows:
        notice_write = upsert_notice(notice_table, notice_arrow)
        result["notice_inserted"] = notice_write.rows_inserted
        result["notice_updated"] = notice_write.rows_updated
    else:
        result["notice_inserted"] = 0
        result["notice_updated"] = 0

    result["notice_total"] = verify_rows(
        catalog, NOTICE_TABLE, notice_expected, NOTICE_KEYS
    )
    result["notice_verified"] = True

    if stop_after_notice:
        result["interrupted_after_notice"] = True
        return result

    if history_rows:
        history_write = history_table.upsert(
            history_arrow,
            join_cols=list(HISTORY_KEYS),
            when_matched_update_all=True,
            when_not_matched_insert_all=True,
        )
        result["history_inserted"] = history_write.rows_inserted
        result["history_updated"] = history_write.rows_updated
    else:
        result["history_inserted"] = 0
        result["history_updated"] = 0

    result["history_total"] = verify_rows(
        catalog, HISTORY_TABLE, history_expected, HISTORY_KEYS
    )
    result["history_verified"] = True

    return result
