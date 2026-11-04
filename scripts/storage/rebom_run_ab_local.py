"""Run verified local A/B Silver preparation and test-table persistence."""

import argparse
from pathlib import Path

from rebom.storage.silver.apply_notice_pipeline import get_local_catalog
from rebom.storage.silver.apply_silver_pipeline import NOTICE_TABLE, HISTORY_TABLE
from rebom.storage.silver.prepare_silver_pipeline import prepare_silver_pipeline
from rebom.storage.silver.run_silver_with_journal import run_silver_with_journal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bronze-manifest", type=Path, required=True)
    parser.add_argument("--event-manifest", type=Path, required=True)
    parser.add_argument("--previous-observed-on")
    parser.add_argument("--journal-dir", type=Path, default=Path.home() / ".rebom" / "ab-runs")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    for path in (args.bronze_manifest, args.event_manifest):
        if not path.is_file():
            parser.error(f"Manifest not found: {path}")

    catalog = get_local_catalog()

    previous_rows = catalog.load_table(NOTICE_TABLE).scan().to_arrow().to_pylist()

    if previous_rows and not args.previous_observed_on:
        parser.error("--previous-observed-on is required for existing notices")

    if args.apply:
        existing_history = catalog.load_table(HISTORY_TABLE).scan().to_arrow().to_pylist()
        fixture_ids = {
            row["desertion_no"]
            for row in previous_rows + existing_history
            if row["desertion_no"].startswith(("AB-SMOKE-", "AB-JOURNAL-"))
        }
        if fixture_ids:
            parser.error("Smoke-test rows exist; use clean integration tables before apply")

    prepared = prepare_silver_pipeline(
        args.bronze_manifest,
        args.event_manifest,
        previous_rows,
        args.previous_observed_on,
    )

    print("RUN_ID:", prepared["run_id"])
    print("PREPARE_READY:", prepared["ready"])

    if not prepared["ready"]:
        print("BLOCKED_STAGE:", prepared["stage"])
        print("CONFLICT_COUNT:", len(prepared["conflicts"]))
        raise SystemExit(1)

    print("NOTICE_DELTA:", len(prepared["notice_delta_rows"]))
    print("STATUS_HISTORY:", len(prepared["history_rows"]))
    print("ACTUAL_CLOSED:", len(prepared["actual_closed_ids"]))
    print("INFERRED_CLOSED:", len(prepared["inferred_closed_ids"]))
    print("LOOKUP_DEFERRED:", len(prepared["lookup_deferred_ids"]))
    print("EVENT_CONFLICTS:", len(prepared["event_conflicts"]))

    if not args.apply:
        print("MODE: DRY_RUN")
        print("ICEBERG_WRITE: NOT_PERFORMED")
        print("RESULT: PASS")
        return

    result = run_silver_with_journal(
        prepared,
        args.journal_dir,
    )

    print("MODE: APPLY")
    print("JOURNAL_STAGE:", result["stage"])
    print("RESULT:", "PASS" if result["stage"] == "COMPLETED" else "INCOMPLETE")


if __name__ == "__main__":
    main()
