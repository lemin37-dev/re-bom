"""AWS Silver dry run: read and validate, never write."""

import argparse
from pathlib import Path

from rebom.storage.silver.aws_catalog import get_aws_catalog
from rebom.storage.silver.prepare_silver_pipeline import prepare_silver_pipeline
from rebom.storage.silver.apply_silver_pipeline import prepare_arrow

PREFIX = "s3://rebom-data-559987919299/bench_tmp/b-storage-test/iceberg/"
NOTICE = "rebom_silver.silver_animal_notice_b_test"
HISTORY = "rebom_silver.silver_animal_status_history_b_test"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bronze-manifest", required=True)
    parser.add_argument("--event-manifest", required=True)
    args = parser.parse_args()

    for path in (args.bronze_manifest, args.event_manifest):
        if not Path(path).is_file():
            raise FileNotFoundError(path)

    catalog = get_aws_catalog()
    notice = catalog.load_table(NOTICE)
    history = catalog.load_table(HISTORY)

    for table in (notice, history):
        location = table.location().rstrip("/") + "/"
        if not location.startswith(PREFIX):
            raise RuntimeError(f"Unexpected table location: {location}")

    previous_rows = notice.scan().to_arrow().to_pylist()
    previous_history = history.scan().to_arrow().to_pylist()

    observed_dates = [
        row["observed_on"]
        for row in previous_history
        if row.get("observed_on")
    ]

    previous_observed_on = max(observed_dates) if observed_dates else None

    print("NOTICE_BEFORE:", len(previous_rows))
    print("HISTORY_BEFORE:", len(previous_history))
    print("PREVIOUS_OBSERVED_ON:", previous_observed_on)

    prepared = prepare_silver_pipeline(
        bronze_manifest_path=args.bronze_manifest,
        event_manifest_path=args.event_manifest,
        previous_silver_rows=previous_rows,
        previous_observed_on=previous_observed_on,
    )

    if not prepared["ready"]:
        raise RuntimeError(
            f"Silver preparation failed: {prepared['stage']}"
        )

    notice_arrow = prepare_arrow(
        prepared["notice_delta_rows"],
        notice,
        ("desertion_no",),
        require_all_fields=True,
    )
    history_arrow = prepare_arrow(
        prepared["history_rows"],
        history,
        ("run_id", "desertion_no", "observed_on", "event_type"),
        require_all_fields=True,
    )

    print("RUN_ID:", prepared["run_id"])
    print("NOTICE_PREPARED:", notice_arrow.num_rows)
    print("HISTORY_PREPARED:", history_arrow.num_rows)
    print("ACTUAL_CLOSED:", len(prepared["actual_closed_ids"]))
    print("INFERRED_CLOSED:", len(prepared["inferred_closed_ids"]))
    print("LOOKUP_DEFERRED:", len(prepared["lookup_deferred_ids"]))
    print("AWS_SILVER_DRY_RUN: PASS")
    print("ICEBERG_WRITE: FALSE")


if __name__ == "__main__":
    main()
