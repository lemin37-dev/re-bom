import pyarrow as pa

from rebom.storage.silver.image_incremental import prepare_safe_image_inserts
from rebom.storage.silver.s3_image_journal import (
    current_image_stage,
    write_image_stage,
)
from rebom.storage.silver.s3_image_snapshot import (
    read_image_snapshot,
    save_image_snapshot,
)


def _index_rows(rows):
    result = {}
    for row in rows:
        key = (row["desertion_no"], row["image_slot"])
        if key in result:
            raise RuntimeError(f"IMAGE_DUPLICATE_KEY: {key}")
        result[key] = row
    return result


def _verify_prepared(catalog, table_name, rows):
    current = _index_rows(
        catalog.load_table(table_name).scan().to_arrow().to_pylist()
    )
    for row in rows:
        key = (row["desertion_no"], row["image_slot"])
        stored = current.get(key)
        if stored is None:
            return False
        if stored["source_url"] != row["source_url"]:
            raise RuntimeError(f"IMAGE_SOURCE_CONFLICT: {key}")
    return True


def run_image_incremental(
    s3, bucket, prefix, run_id, catalog,
    table_name, expected_location, raw_notices,
):
    table = catalog.load_table(table_name)
    if table.location().rstrip("/") != expected_location.rstrip("/"):
        raise RuntimeError("IMAGE_TABLE_LOCATION_MISMATCH")

    snapshot = read_image_snapshot(s3, bucket, prefix, run_id)
    stage = current_image_stage(s3, bucket, prefix, run_id)

    if stage is not None and snapshot is None:
        raise RuntimeError("IMAGE_JOURNAL_WITHOUT_SNAPSHOT")

    if snapshot is None:
        existing = table.scan().to_arrow().to_pylist()
        rows = prepare_safe_image_inserts(raw_notices, existing)
        save_image_snapshot(
            s3, bucket, prefix, run_id,
            table_name, expected_location, rows,
        )
        snapshot = read_image_snapshot(s3, bucket, prefix, run_id)
        if snapshot is None:
            raise RuntimeError("IMAGE_SNAPSHOT_NOT_SAVED")

    if (
        snapshot["table_name"] != table_name
        or snapshot["expected_location"].rstrip("/")
        != expected_location.rstrip("/")
    ):
        raise RuntimeError("IMAGE_SNAPSHOT_TARGET_MISMATCH")

    rows = snapshot["rows"]

    if stage is None:
        write_image_stage(s3, bucket, prefix, run_id, "PREPARED")

    current = _index_rows(
        catalog.load_table(table_name).scan().to_arrow().to_pylist()
    )

    missing = []
    for row in rows:
        key = (row["desertion_no"], row["image_slot"])
        stored = current.get(key)

        if stored is None:
            missing.append(row)
        elif stored["source_url"] != row["source_url"]:
            raise RuntimeError(f"IMAGE_SOURCE_CONFLICT: {key}")

    if stage in {"IMAGE_APPLIED", "COMPLETED"} and missing:
        raise RuntimeError("IMAGE_JOURNAL_AHEAD_OF_ICEBERG")

    if missing:
        if len(missing) != len(rows):
            raise RuntimeError("IMAGE_PARTIAL_APPLY_REQUIRES_REVIEW")

        arrow = pa.Table.from_pylist(
            missing,
            schema=table.schema().as_arrow(),
        )
        catalog.load_table(table_name).append(arrow)

    if not _verify_prepared(catalog, table_name, rows):
        raise RuntimeError("IMAGE_APPLY_VERIFY_FAILED")

    write_image_stage(s3, bucket, prefix, run_id, "IMAGE_APPLIED")
    write_image_stage(s3, bucket, prefix, run_id, "COMPLETED")

    if current_image_stage(s3, bucket, prefix, run_id) != "COMPLETED":
        raise RuntimeError("IMAGE_JOURNAL_NOT_COMPLETED")

    return {
        "run_id": run_id,
        "prepared": len(rows),
        "inserted": len(missing),
        "stage": "COMPLETED",
    }
