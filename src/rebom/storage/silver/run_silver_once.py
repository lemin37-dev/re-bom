"""Run one explicitly selected Bronze-to-Silver processing job."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import boto3

from rebom.storage.silver.environment_config import get_silver_environment
from rebom.storage.silver.fetch_run_inputs import (
    RUN_ID_PATTERN,
    fetch_run_inputs,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--environment", choices=("test", "prod"), default="test")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--apply-image", action="store_true")
    parser.add_argument("--check-image", action="store_true")
    parser.add_argument(
        "--s3-state-prefix",
        default=None,
    )
    args = parser.parse_args()
    config = get_silver_environment(args.environment)
    if args.s3_state_prefix is None:
        args.s3_state_prefix = config.s3_state_prefix

    if not RUN_ID_PATTERN.fullmatch(args.run_id):
        parser.error("Invalid run_id")

    if not args.bucket:
        parser.error("Bucket is required")

    if args.apply_image and not args.apply:
        parser.error("--apply-image requires --apply")

    if args.check_image and args.apply_image:
        parser.error("--check-image cannot be combined with --apply-image")



    root = Path.cwd()
    runner = root / "scripts/storage/rebom_run_aws_real_silver_test.py"

    if not runner.is_file():
        raise FileNotFoundError(runner)

    s3 = boto3.client("s3", region_name="ap-northeast-2")

    with tempfile.TemporaryDirectory(prefix="rebom-silver-") as tmp:
        directory = Path(tmp)

        fetched = fetch_run_inputs(
            s3,
            args.bucket,
            args.run_id,
            directory / "inputs",
        )

        print("INPUT_VERIFIED:", fetched["run_id"], flush=True)

        command = [
            sys.executable,
            str(runner),
            "--environment",
            args.environment,
            "--bronze-manifest",
            fetched["bronze_manifest"],
            "--event-manifest",
            fetched["event_manifest"],
            "--journal-dir",
            str(directory / "journal"),
        ]

        if args.apply:
            command.extend([
                "--apply",
                "--s3-state-bucket",
                args.bucket,
                "--s3-state-prefix",
                args.s3_state_prefix,
            ])

        subprocess.run(command, check=True, cwd=root)


        if args.apply_image or args.check_image:
            from rebom.storage.silver.aws_catalog import get_aws_catalog
            from rebom.storage.silver.load_bronze import load_bronze_run
            from rebom.storage.silver.run_image_incremental import run_image_incremental

            bronze = load_bronze_run(fetched["bronze_manifest"])

            if bronze["run_id"] != args.run_id:
                raise RuntimeError("IMAGE_BRONZE_RUN_ID_MISMATCH")

            raw_notices = []
            for kind in ("updated", "list_notice", "list_protect"):
                raw_notices.extend(bronze["rows_by_kind"][kind])

            image_table = config.image_table
            image_location = config.table_location(image_table)

            if args.check_image:
                from rebom.storage.silver.image_incremental import (
                    prepare_safe_image_inserts,
                )

                catalog = get_aws_catalog()
                table = catalog.load_table(image_table)

                if table.location().rstrip("/") != image_location.rstrip("/"):
                    raise RuntimeError("IMAGE_TABLE_LOCATION_MISMATCH")

                existing = table.scan().to_arrow().to_pylist()
                new_rows = prepare_safe_image_inserts(raw_notices, existing)

                print("IMAGE_EXISTING:", len(existing))
                print("IMAGE_NEW_PREPARED:", len(new_rows))
                print("IMAGE_DRY_RUN: PASS")
                print("IMAGE_ICEBERG_WRITE: FALSE")
                print("SILVER_ONCE: PASS")
                return

            result = run_image_incremental(
                s3=s3,
                bucket=args.bucket,
                prefix=args.s3_state_prefix,
                run_id=args.run_id,
                catalog=get_aws_catalog(),
                table_name=image_table,
                expected_location=image_location,
                raw_notices=raw_notices,
            )

            print("IMAGE_INCREMENTAL_RESULT:", result)

    print("SILVER_ONCE: PASS")



if __name__ == "__main__":
    main()
