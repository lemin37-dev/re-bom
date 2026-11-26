"""Run one explicitly selected Bronze-to-Silver processing job."""

import argparse
import os
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
    parser.add_argument("--check-photo-manifests", action="store_true")
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

    # 옛 실험 경로(2026-11-18 이후 쓰지 않음). image_table 은 이제 rescue_ingest 사진 처리 표라서
    # 이 경로로 쓰면 안 된다. 아무것도 실행하기 전에 멈춘다.
    if args.apply_image or args.check_image:
        parser.error("IMAGE_EXPERIMENT_RETIRED: 사진은 rescue_ingest 사진 처리가 Silver 이미지 표에 쓴다")

    if args.apply_image and not args.apply:
        parser.error("--apply-image requires --apply")

    if args.check_image and args.apply_image:
        parser.error("--check-image cannot be combined with --apply-image")

    if args.check_photo_manifests and args.apply:
        parser.error("--check-photo-manifests requires DRY_RUN")

    if args.check_photo_manifests and args.check_image:
        parser.error("--check-photo-manifests cannot be combined with --check-image")



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
            command.append("--apply")

        command.extend([
            "--s3-state-bucket",
            args.bucket,
            "--s3-state-prefix",
            args.s3_state_prefix,
        ])

        subprocess.run(command, check=True, cwd=root)

        if args.apply and args.environment == "test":
            from rebom.storage.silver.aws_catalog import get_aws_catalog
            from rebom.storage.silver.load_bronze import load_bronze_run
            from rebom.storage.silver.publish_log import (
                build_notice_snapshot_evidence,
                find_completed_notice_snapshot_id,
                save_notice_completion_s3,
            )

            bronze = load_bronze_run(fetched["bronze_manifest"])

            if bronze["run_id"] != args.run_id:
                raise RuntimeError("NOTICE_BRONZE_RUN_ID_MISMATCH")

            catalog = get_aws_catalog()
            notice_table = catalog.load_table(config.notice_table)

            snapshot_id = find_completed_notice_snapshot_id(
                s3,
                args.bucket,
                args.s3_state_prefix,
                args.run_id,
                notice_table,
            )

            record = build_notice_snapshot_evidence(
                bronze_run_id=args.run_id,
                notice_table=config.notice_table,
                notice_snapshot_id=snapshot_id,
                source_fetched_at=bronze["fetched_at"],
            )

            record["status"] = "COMPLETED"

            completion_prefix = (
                "bench_tmp/b-storage-test/notice_completion"
            )

            result = save_notice_completion_s3(
                s3,
                args.bucket,
                completion_prefix,
                record,
            )

            print("NOTICE_COMPLETION:", result)
            print("NOTICE_SNAPSHOT_ID:", snapshot_id)
            print(
                "NOTICE_COMPLETION_KEY:",
                f"{completion_prefix}/{args.run_id}.json",
            )


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

    if args.check_photo_manifests:
        if args.environment != "test":
            raise RuntimeError("PHOTO_MANIFEST_CHECK_TEST_ONLY")

        photo_script = root / "scripts/storage/rebom_auto_process_photo_manifests.py"

        if not photo_script.is_file():
            raise FileNotFoundError(photo_script)

        photo_env = os.environ.copy()
        photo_env.update({
            "REBOM_S3_BUCKET": args.bucket,
            "REBOM_PHOTO_ICEBERG_TABLE": (
                "rebom_silver.silver_animal_image_auto_b_test"
            ),
            "REBOM_PHOTO_ICEBERG_LOCATION": (
                f"s3://{args.bucket}/bench_tmp/b-storage-test/"
                "iceberg/silver_animal_image_auto_b_test"
            ),
            "REBOM_PHOTO_HISTORY_TABLE": (
                "rebom_silver.silver_animal_image_version_history_auto_b_test"
            ),
            "REBOM_PHOTO_HISTORY_LOCATION": (
                f"s3://{args.bucket}/bench_tmp/b-storage-test/"
                "iceberg/silver_animal_image_version_history_auto_b_test"
            ),
            "REBOM_PHOTO_JOURNAL_PREFIX": (
                "bench_tmp/b-storage-test/journals/photo_auto_runs"
            ),
        })

        subprocess.run(
            [
                sys.executable,
                str(photo_script),
                "--bronze-run-id",
                args.run_id,
                "--defer-missing-manifest",
            ],
            check=True,
            cwd=root,
            env=photo_env,
        )

    print("SILVER_ONCE: PASS")



if __name__ == "__main__":
    main()
