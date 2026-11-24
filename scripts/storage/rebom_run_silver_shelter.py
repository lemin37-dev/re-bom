"""??? Silver ?? ?? ???."""

import argparse
import hashlib
import json
from pathlib import Path
from collections import Counter

import boto3

from rebom.storage.silver.aws_catalog import get_aws_catalog
from rebom.storage.silver.load_bronze import load_bronze_run
from rebom.storage.silver.shelter_incremental import prepare_shelter_delta
from rebom.storage.silver.shelter_apply import (
    SHELTER_TABLE,
    BOOTSTRAP_RUN_ID,
    check_run_order,
    run_time,
    validate_shelter_table,
    apply_shelter_delta,
)
from rebom.storage.silver.shelter_journal import (
    shelter_writer_lock,
    list_run_ids,
    save_prepared,
    load_prepared,
    save_completed,
    load_completed,
)

BUCKET = "rebom-data-559987919299"


def execute(args, s3, catalog):
    manifest = Path(args.manifest)

    raw_manifest = manifest.read_bytes()
    metadata = json.loads(raw_manifest.decode("utf-8-sig"))
    run_id = metadata["run_id"]

    run_time(run_id)

    checksum = hashlib.sha256(raw_manifest).hexdigest()
    source_fetched_at = metadata["fetched_at"]

    bronze = load_bronze_run(str(manifest))

    table = catalog.load_table(SHELTER_TABLE)
    validate_shelter_table(table)

    completed = load_completed(s3, BUCKET, run_id)

    if completed is not None:
        if completed["manifest_checksum"] != checksum:
            raise RuntimeError("SHELTER_INPUT_CHECKSUM_MISMATCH")

        if completed["shelter_table"] != SHELTER_TABLE:
            raise RuntimeError("SHELTER_COMPLETION_TABLE_MISMATCH")

        if table.snapshot_by_id(completed["shelter_snapshot_id"]) is None:
            raise RuntimeError("SHELTER_COMPLETED_SNAPSHOT_MISSING")

        print("SHELTER_RUN: COMPLETED_RETRY")
        print("ICEBERG_WRITE: FALSE")
        print(json.dumps(completed, ensure_ascii=False))
        return

    completed_ids = [BOOTSTRAP_RUN_ID]
    pending = []

    for other in list_run_ids(s3, BUCKET):
        if load_completed(s3, BUCKET, other) is not None:
            completed_ids.append(other)
        else:
            pending.append(other)

    if any(other != run_id for other in pending):
        raise RuntimeError(f"UNFINISHED_SHELTER_RUNS: {pending}")

    latest = max(completed_ids, key=run_time)
    order = check_run_order(run_id, latest)

    print("SHELTER_RUN_ORDER:", order)

    prepared = load_prepared(s3, BUCKET, run_id)

    if prepared is not None:
        if prepared["manifest_checksum"] != checksum:
            raise RuntimeError("SHELTER_INPUT_CHECKSUM_MISMATCH")

        if prepared["shelter_table"] != SHELTER_TABLE:
            raise RuntimeError("SHELTER_PREPARED_TABLE_MISMATCH")

        print("SHELTER_PREPARED: RESTORED")

    else:
        raw_rows = [
            row
            for kind in ("updated", "list_notice", "list_protect")
            for row in bronze["rows_by_kind"][kind]
        ]

        previous = table.scan().to_arrow().to_pylist()
        delta = prepare_shelter_delta(raw_rows, previous)

        prepared = {
            "run_id": run_id,
            "source_fetched_at": source_fetched_at,
            "source_type": "animal_notice",
            "manifest_checksum": checksum,
            "shelter_table": SHELTER_TABLE,
            "base_snapshot_id": table.current_snapshot().snapshot_id,
            "insert_count": len(delta["insert_rows"]),
            "update_count": len(delta["update_rows"]),
            "unchanged_count": len(delta["unchanged_keys"]),
            "not_observed_count": len(delta["not_observed_keys"]),
            "conflict_count": len(delta["conflicts"]),
            "missing_key_count": len(delta["missing_keys"]),
            "conflict_fields": dict(Counter(
                c["field"] for c in delta["conflicts"]
            )),
            "delta_rows": delta["delta_rows"],
            "changes": delta["changes"],
            "conflicts": delta["conflicts"],
            "missing_keys": delta["missing_keys"],
        }

    print("RUN_ID:", run_id)

    for field in (
        "insert_count",
        "update_count",
        "unchanged_count",
        "not_observed_count",
        "conflict_count",
        "missing_key_count",
    ):
        print(field.upper() + ":", prepared[field])

    if not args.apply:
        print("ICEBERG_WRITE: FALSE")
        print("SHELTER_DRY_RUN: PASS")
        return

    save_prepared(s3, BUCKET, run_id, prepared)

    print("SHELTER_PREPARED: VERIFIED")

    table.refresh()

    current_snapshot = table.current_snapshot()

    if current_snapshot is None:
        raise RuntimeError("SHELTER_BASE_SNAPSHOT_MISSING")

    if current_snapshot.snapshot_id != prepared["base_snapshot_id"]:
        stored = table.scan().to_arrow().to_pylist()
        by_key = {row["care_reg_no"]: row for row in stored}

        if len(by_key) != len(stored):
            raise RuntimeError("DUPLICATE_SHELTER_KEYS")

        expected = prepared["delta_rows"]

        if not expected or not all(
            by_key.get(row["care_reg_no"]) == row
            for row in expected
        ):
            raise RuntimeError("SHELTER_BASE_SNAPSHOT_CHANGED")

        print("SHELTER_WRITE: RECOVERED_ALREADY_APPLIED")

    else:
        result = apply_shelter_delta(
            table,
            prepared["delta_rows"],
        )

        print("ACTUAL_INSERTED:", result["inserted"])
        print("ACTUAL_UPDATED:", result["updated"])
        print("ICEBERG_WRITE:", result["iceberg_write"])

    table.refresh()

    record = {
        "run_id": run_id,
        "source_fetched_at": source_fetched_at,
        "source_type": "animal_notice",
        "manifest_checksum": checksum,
        "shelter_table": SHELTER_TABLE,
        "shelter_snapshot_id": table.current_snapshot().snapshot_id,
        "status": "COMPLETED",
        "insert_count": prepared["insert_count"],
        "update_count": prepared["update_count"],
        "conflict_count": prepared["conflict_count"],
        "missing_key_count": prepared["missing_key_count"],
    }

    save_completed(s3, BUCKET, run_id, record)

    print("SHELTER_SNAPSHOT:", record["shelter_snapshot_id"])
    print("SHELTER_APPLY: PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    session = boto3.Session(
        profile_name=args.profile,
        region_name="ap-northeast-2",
    )

    s3 = session.client("s3")
    catalog = get_aws_catalog(profile=args.profile)

    metadata = json.loads(
        Path(args.manifest).read_text(encoding="utf-8-sig")
    )

    if args.apply:
        with shelter_writer_lock(s3, BUCKET, metadata["run_id"]):
            execute(args, s3, catalog)
    else:
        execute(args, s3, catalog)


if __name__ == "__main__":
    main()
