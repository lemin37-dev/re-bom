"""Validate and optionally UPSERT prepared Silver rows into isolated AWS Glue Iceberg tables.

Usage:
  python scripts/storage/rebom_run_aws_silver.py --bronze-manifest PATH --event-manifest PATH
  python scripts/storage/rebom_run_aws_silver.py --bronze-manifest PATH --event-manifest PATH --apply

Requires manifest files and their referenced data files to be readable by the existing loaders.
The local journal and prepared parquet snapshot must be kept for reliable retries.
"""

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from rebom.storage.silver.apply_silver_pipeline import prepare_arrow
from rebom.storage.silver.aws_catalog import get_aws_catalog
from rebom.storage.silver.environment_config import get_silver_environment
from rebom.storage.silver.prepare_silver_pipeline import prepare_silver_pipeline
from rebom.storage.silver.reconcile_run_journal import reconcile_run
from rebom.storage.silver.run_journal import journal_path, read_run
from rebom.storage.silver.s3_run_snapshot import save_snapshot, restore_snapshot
from rebom.storage.silver.s3_run_journal import current_stage, write_stage
from rebom.storage.silver.run_journal import STAGES
import boto3


TEST_CONFIG = get_silver_environment("test")
PREFIX = f"{TEST_CONFIG.iceberg_prefix.rstrip('/')}/"
NOTICE_NAME = TEST_CONFIG.notice_table
HISTORY_NAME = TEST_CONFIG.history_table
NOTICE_KEYS = ("desertion_no",)
HISTORY_KEYS = ("run_id", "desertion_no", "observed_on", "event_type")
DEFAULT_JOURNAL = Path(".local") / "rebom" / "aws-real-20261111-journal"


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_table(table, expected_name):
    expected = f"{PREFIX}{expected_name}"
    actual = table.location().rstrip("/")
    if actual != expected:
        raise RuntimeError(f"Unsafe Iceberg table location: {actual}; expected: {expected}")


def get_tables():
    catalog = get_aws_catalog()
    notice = catalog.load_table(NOTICE_NAME)
    history = catalog.load_table(HISTORY_NAME)
    validate_table(notice, NOTICE_NAME.split(".")[-1])
    validate_table(history, HISTORY_NAME.split(".")[-1])
    return catalog, notice, history


def index_rows(rows, keys):
    result = {}
    for row in rows:
        key = tuple(row.get(k) for k in keys)
        if any(value is None or value == "" for value in key):
            raise ValueError(f"Missing Iceberg key {keys}: {key}")
        if key in result:
            raise ValueError(f"Duplicate Iceberg key {keys}: {key}")
        result[key] = row
    return result


def verified_stored(catalog, name, expected_arrow, keys):
    if expected_arrow.num_rows == 0:
        return True  # Explicit no-op stage: no rows to write.
    expected = index_rows(expected_arrow.to_pylist(), keys)
    actual = index_rows(catalog.load_table(name).scan().to_arrow().to_pylist(), keys)
    return all(actual.get(key) == value for key, value in expected.items())


def load_history_observation(history_rows):
    dates = [row["observed_on"] for row in history_rows if row.get("observed_on")]
    return max(dates) if dates else None


def prepare_new(bronze_path, event_path, notice, history):
    previous_notice = notice.scan().to_arrow().to_pylist()
    previous_history = history.scan().to_arrow().to_pylist()
    prepared = prepare_silver_pipeline(
        bronze_manifest_path=str(bronze_path),
        event_manifest_path=str(event_path),
        previous_silver_rows=previous_notice,
        previous_observed_on=load_history_observation(previous_history),
    )
    if not prepared.get("ready"):
        raise RuntimeError(f"Silver preparation failed: {prepared.get('stage')}; {prepared.get('conflicts')}")

    notice_arrow = prepare_arrow(
        prepared["notice_delta_rows"], notice, NOTICE_KEYS, require_all_fields=True
    )
    history_arrow = prepare_arrow(
        prepared["history_rows"], history, HISTORY_KEYS, require_all_fields=True
    )
    if any(row["run_id"] != prepared["run_id"] for row in history_arrow.to_pylist()):
        raise ValueError("History run_id mismatch")
    return prepared, notice_arrow, history_arrow


def assert_schema(arrow, table):
    if not arrow.schema.equals(table.schema().as_arrow(), check_metadata=False):
        raise RuntimeError(f"Iceberg schema changed since preparation: {table.name()}")


def persisted_snapshot(run_dir, hashes, notice, history):
    metadata = json.loads((run_dir / "inputs.json").read_text(encoding="utf-8"))
    if metadata["hashes"] != hashes:
        raise RuntimeError("Existing run_id has different manifest content; refusing to reuse")
    if metadata["locations"] != [notice.location(), history.location()]:
        raise RuntimeError("Iceberg destination changed since preparation")
    notice_arrow = pq.read_table(run_dir / "notice.parquet")
    history_arrow = pq.read_table(run_dir / "history.parquet")
    assert_schema(notice_arrow, notice)
    assert_schema(history_arrow, history)
    return metadata["run_id"], notice_arrow, history_arrow


def store_snapshot(run_dir, run_id, hashes, notice, history, notice_arrow, history_arrow):
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".prepare-", dir=run_dir.parent))
    try:
        pq.write_table(notice_arrow, staging / "notice.parquet")
        pq.write_table(history_arrow, staging / "history.parquet")
        metadata = {
            "run_id": run_id,
            "hashes": hashes,
            "locations": [notice.location(), history.location()],
        }
        (staging / "inputs.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if run_dir.exists():
            raise RuntimeError(f"Prepared snapshot already exists: {run_dir}")
        os.rename(staging, run_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def output(run_id, notice_rows, history_rows, mode):
    print("RUN_ID:", run_id)
    print("NOTICE_PREPARED:", notice_rows)
    print("HISTORY_PREPARED:", history_rows)
    print("MODE:", mode)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bronze-manifest", type=Path, required=True)
    parser.add_argument("--event-manifest", type=Path, required=True)
    parser.add_argument("--environment", choices=("test", "prod"), default="test")
    parser.add_argument("--apply", action="store_true", help="Write to isolated B-test Glue tables")
    parser.add_argument("--journal-dir", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--s3-state-bucket")
    parser.add_argument("--s3-state-prefix", default="silver/rebom/journal")
    return parser.parse_args()


def execute(args):
    get_silver_environment(args.environment)
    for path in (args.bronze_manifest, args.event_manifest):
        if not path.is_file():
            raise FileNotFoundError(path)
    hashes = [sha256_file(args.bronze_manifest), sha256_file(args.event_manifest)]
    catalog, notice, history = get_tables()

    # The Bronze manifest gives the stable identity before any table-dependent preparation.
    bronze_manifest = json.loads(args.bronze_manifest.read_text(encoding="utf-8-sig"))
    run_id = bronze_manifest.get("run_id")
    if not isinstance(run_id, str):
        raise ValueError("Bronze manifest must contain run_id")
    journal_path(run_id, args.journal_dir)  # Validate run_id before using it in a path.
    run_dir = args.journal_dir / "prepared" / run_id
    record = read_run(run_id, args.journal_dir)

    state_s3 = None
    if args.apply and args.s3_state_bucket:
        state_s3 = boto3.client("s3", region_name="ap-northeast-2")
        if not run_dir.exists():
            restored = restore_snapshot(
                state_s3,
                args.s3_state_bucket,
                args.s3_state_prefix,
                run_id,
                run_dir,
            )
            if restored:
                print("S3_PREPARED_SNAPSHOT: RESTORED")

    if run_dir.exists():
        run_id, notice_arrow, history_arrow = persisted_snapshot(
            run_dir, hashes, notice, history
        )
        print("PREPARED_SNAPSHOT: REUSED")
    else:
        if record is not None:
            raise RuntimeError("Journal exists but prepared snapshot is missing; investigate")
        prepared, notice_arrow, history_arrow = prepare_new(
            args.bronze_manifest, args.event_manifest, notice, history
        )
        if prepared["run_id"] != run_id:
            raise RuntimeError("Run ID differs from verified Bronze manifest")
        assert_schema(notice_arrow, notice)
        assert_schema(history_arrow, history)
        if args.apply:
            store_snapshot(
                run_dir, run_id, hashes, notice, history, notice_arrow, history_arrow
            )
            print("PREPARED_SNAPSHOT: SAVED")

    if args.apply and state_s3 is not None:
        save_snapshot(
            state_s3,
            args.s3_state_bucket,
            args.s3_state_prefix,
            run_id,
            run_dir,
        )
        print("S3_PREPARED_SNAPSHOT: VERIFIED")

    output(run_id, notice_arrow.num_rows, history_arrow.num_rows,
           "APPLY" if args.apply else "DRY_RUN")
    if not args.apply:
        print("AWS_SILVER_DRY_RUN: PASS")
        print("ICEBERG_WRITE: FALSE")
        return

    def reconcile():
        nv = verified_stored(catalog, NOTICE_NAME, notice_arrow, NOTICE_KEYS)
        hv = verified_stored(catalog, HISTORY_NAME, history_arrow, HISTORY_KEYS)
        if history_arrow.num_rows == 0 and not nv:
            hv = False  # No-op history must not precede an unverified notice stage.
        # Empty datasets are explicitly no-op stages; their successful preparation
        # is the verification evidence, rather than an Iceberg row.
        result = reconcile_run(run_id, args.journal_dir, nv, hv)

        if state_s3 is not None:
            remote_stage = current_stage(
                state_s3,
                args.s3_state_bucket,
                args.s3_state_prefix,
                run_id,
            )

            if remote_stage is not None:
                if STAGES.index(remote_stage) > STAGES.index(result["stage"]):
                    raise RuntimeError(
                        "S3 Journal is ahead of verified Iceberg state"
                    )

            start_index = (
                STAGES.index(remote_stage) + 1
                if remote_stage is not None else 0
            )

            for stage in STAGES[start_index:STAGES.index(result["stage"]) + 1]:
                write_stage(
                    state_s3,
                    args.s3_state_bucket,
                    args.s3_state_prefix,
                    run_id,
                    stage,
                )

            print("S3_JOURNAL_STAGE:", result["stage"])

        return result

    before = reconcile()
    if before["stage"] == "COMPLETED":
        print("AWS_SILVER_APPLY: REUSED_ALREADY_VERIFIED")
        return

    if notice_arrow.num_rows and before["stage"] == "PREPARED":
        result = notice.upsert(
            notice_arrow,
            join_cols=list(NOTICE_KEYS),
            when_matched_update_all=True,
            when_not_matched_insert_all=True,
        )
        print("NOTICE_INSERTED:", result.rows_inserted)
        print("NOTICE_UPDATED:", result.rows_updated)

    after_notice = reconcile()
    if after_notice["stage"] not in ("NOTICE_APPLIED", "HISTORY_APPLIED", "COMPLETED"):
        raise RuntimeError("Notice stage was not verified")

    if history_arrow.num_rows and after_notice["stage"] != "COMPLETED":
        result = history.upsert(
            history_arrow,
            join_cols=list(HISTORY_KEYS),
            when_matched_update_all=True,
            when_not_matched_insert_all=True,
        )
        print("HISTORY_INSERTED:", result.rows_inserted)
        print("HISTORY_UPDATED:", result.rows_updated)

    final = reconcile()
    if final["stage"] != "COMPLETED":
        raise RuntimeError(f"Not completed; verified stage: {final['stage']}")
    print("AWS_SILVER_APPLY: PASS")
    print("JOURNAL_STAGE:", final["stage"])


def main():
    args = parse_args()
    if not args.apply:
        execute(args)
        return

    # Serialize every writer targeting these two shared test tables, across run IDs.
    # This remains a local-process lock and is NOT safe across hosts / Airflow pods.
    # An externally coordinated single-writer policy is required for deployment.
    lock = args.journal_dir / "aws-silver-tables.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(
            f"AWS Silver tables locked: {lock}; check active writer before removing"
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
        # Lock covers source snapshot creation, journal reconciliation, and writes.
        execute(args)
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
