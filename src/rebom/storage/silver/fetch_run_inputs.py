"""Download and verify a matched Bronze and Silver events run from S3."""

import argparse
import re
from pathlib import Path

import boto3

from rebom.storage.silver.load_bronze import load_bronze_run
from rebom.storage.silver.load_silver_events import load_silver_events


RUN_ID_PATTERN = re.compile(r"\d{8}T\d{6}-[0-9a-fA-F]{6}")


def fetch_run_inputs(s3, bucket, run_id, target_root):
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("Invalid run_id")

    dt = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"

    root = Path(target_root)
    bronze_dir = root / "bronze" / f"run_id={run_id}"
    events_dir = root / "events" / f"run_id={run_id}"

    sources = (
        (
            "bronze/rescue/raw",
            bronze_dir,
            ("manifest.json", "updated.jsonl.gz",
             "list_notice.jsonl.gz", "list_protect.jsonl.gz"),
        ),
        (
            "silver/rescue/events",
            events_dir,
            ("manifest.json",),
        ),
    )

    for prefix, directory, filenames in sources:
        directory.mkdir(parents=True, exist_ok=True)

        for filename in filenames:
            key = f"{prefix}/dt={dt}/run_id={run_id}/{filename}"
            s3.download_file(bucket, key, str(directory / filename))

    from json import loads

    event_manifest = loads(
        (events_dir / "manifest.json").read_text(encoding="utf-8-sig")
    )

    if event_manifest.get("bronze_run_id") != run_id:
        raise ValueError("Event and Bronze run_id mismatch")

    for item in event_manifest.get("files", []):
        kind = item.get("kind")
        filename = item.get("file")

        if kind not in ("events", "lookup"):
            raise ValueError("Unexpected event file kind")
        if filename != f"{kind}.jsonl.gz":
            raise ValueError("Unexpected event file name")

        key = f"silver/rescue/events/dt={dt}/run_id={run_id}/{filename}"
        s3.download_file(bucket, key, str(events_dir / filename))

    bronze = load_bronze_run(bronze_dir / "manifest.json")
    events = load_silver_events(events_dir / "manifest.json", run_id)

    if bronze["run_id"] != events["bronze_run_id"]:
        raise ValueError("Verified run_id mismatch")

    return {
        "run_id": run_id,
        "bronze_manifest": str(bronze_dir / "manifest.json"),
        "event_manifest": str(events_dir / "manifest.json"),
        "bronze_counts": {
            kind: len(rows)
            for kind, rows in bronze["rows_by_kind"].items()
        },
        "event_count": len(events["rows_by_kind"]["events"]),
        "incomplete_partitions": len(bronze["incomplete_partitions"]),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name="ap-northeast-2")

    result = fetch_run_inputs(
        s3, args.bucket, args.run_id, args.output_dir
    )

    for key, value in result.items():
        print(f"{key.upper()}: {value}")

    print("S3_SILVER_INPUT_VERIFICATION: PASS")


if __name__ == "__main__":
    main()
