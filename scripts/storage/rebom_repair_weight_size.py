import argparse
import json

import boto3

from rebom.storage.silver.aws_catalog import get_aws_catalog
from rebom.storage.silver.environment_config import get_silver_environment
from rebom.storage.silver.apply_silver_pipeline import (
    NOTICE_KEYS,
    index_rows,
    prepare_arrow,
    upsert_notice,
)

from rebom.storage.silver.s3_run_journal import silver_writer_lock

BUCKET = "rebom-data-559987919299"
ACCOUNT = "559987919299"
REGION = "ap-northeast-2"
EXPECTED_COUNT = 793


def plan_repair(rows):
    changes = []

    for row in rows:
        if (
            row["weight_max_kg"] == 10
            and row["size"] == "small"
        ):
            updated = dict(row)
            updated["size"] = "medium"
            changes.append(updated)

    return changes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    session = boto3.Session(region_name=REGION)

    if session.client("sts").get_caller_identity()["Account"] != ACCOUNT:
        raise RuntimeError("UNEXPECTED_AWS_ACCOUNT")

    if args.apply:
        with silver_writer_lock(
            session.client("s3"),
            BUCKET,
            "test",
            "weight-size-repair",
        ):
            execute(args)
    else:
        execute(args)


def execute(args):
    session = boto3.Session(region_name=REGION)

    if session.client("sts").get_caller_identity()["Account"] != ACCOUNT:
        raise RuntimeError("UNEXPECTED_AWS_ACCOUNT")

    env = get_silver_environment("test")
    catalog = get_aws_catalog(region=REGION)
    table = catalog.load_table(env.notice_table)

    if table.location().rstrip("/") != env.table_location(
        env.notice_table
    ).rstrip("/"):
        raise RuntimeError("UNEXPECTED_TABLE_LOCATION")

    snapshot = table.current_snapshot()
    rows = table.scan().to_arrow().to_pylist()

    index_rows(rows, NOTICE_KEYS)
    changes = plan_repair(rows)

    if changes:
        prepare_arrow(
            changes,
            table,
            NOTICE_KEYS,
            require_all_fields=True,
        )

    before = index_rows(rows, NOTICE_KEYS)

    for row in changes:
        key = (row["desertion_no"],)
        original = before[key]

        changed_fields = {
            field
            for field in row
            if row[field] != original[field]
        }

        if changed_fields != {"size"}:
            raise RuntimeError("UNEXPECTED_COLUMN_CHANGE")

    result = {
        "mode": "DRY_RUN",
        "table": env.notice_table,
        "before_snapshot": snapshot.snapshot_id,
        "total_rows": len(rows),
        "update_count": len(changes),
        "insert_count": 0,
        "protecting": sum(
            row["status_group"] == "PROTECTING"
            for row in changes
        ),
        "closed": sum(
            row["status_group"] == "CLOSED"
            for row in changes
        ),
        "changed_columns": ["size"] if changes else [],
        "aws_write": False,
    }

    print(json.dumps(result, indent=2))

    if len(changes) not in (0, EXPECTED_COUNT):
        raise RuntimeError("UNEXPECTED_UPDATE_COUNT")

    print("WEIGHT_SIZE_REPAIR_DRY_RUN: PASS")

    if not args.apply:
        return

    if not changes:
        print("WEIGHT_SIZE_ALREADY_REPAIRED")
        print("ICEBERG_WRITE: False")
        return

    latest = catalog.load_table(env.notice_table)

    if latest.current_snapshot().snapshot_id != snapshot.snapshot_id:
        raise RuntimeError("SNAPSHOT_CHANGED")

    arrow = prepare_arrow(
        changes,
        latest,
        NOTICE_KEYS,
        require_all_fields=True,
    )

    expected = dict(before)
    expected.update(index_rows(changes, NOTICE_KEYS))

    history_before = catalog.load_table(
        env.history_table
    ).current_snapshot().snapshot_id

    written = upsert_notice(latest, arrow)

    if written.rows_inserted != 0:
        raise RuntimeError("UNEXPECTED_INSERT")

    if written.rows_updated != len(changes):
        raise RuntimeError("UNEXPECTED_UPDATE_COUNT")

    after_table = catalog.load_table(env.notice_table)
    after_rows = after_table.scan().to_arrow().to_pylist()
    actual = index_rows(after_rows, NOTICE_KEYS)

    if actual != expected:
        raise RuntimeError("DATA_VERIFICATION_FAILED")

    history_after = catalog.load_table(
        env.history_table
    ).current_snapshot().snapshot_id

    if history_before != history_after:
        raise RuntimeError("HISTORY_SNAPSHOT_CHANGED")

    if plan_repair(after_rows):
        raise RuntimeError("REPAIR_INCOMPLETE")

    print("UPDATED:", written.rows_updated)
    print("INSERTED:", written.rows_inserted)
    print("TOTAL_ROWS:", len(after_rows))
    print("BEFORE_SNAPSHOT:", snapshot.snapshot_id)
    print("AFTER_SNAPSHOT:", after_table.current_snapshot().snapshot_id)
    print("HISTORY_UNCHANGED: PASS")
    print("ALL_ROWS_VERIFIED: PASS")
    print("WEIGHT_SIZE_REPAIR_APPLY: PASS")


if __name__ == "__main__":
    main()
