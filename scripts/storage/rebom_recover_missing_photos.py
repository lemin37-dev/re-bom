import argparse
import json

import boto3

from rebom.storage.silver.aws_catalog import get_aws_catalog
from rebom.storage.silver.photo_version_history import (
    apply_photo_current_updates,
)

BUCKET = "rebom-data-559987919299"
REGION = "ap-northeast-2"
ACCOUNT = "559987919299"

NOTICE_TABLE = "rebom_silver.silver_animal_notice_real_20261111_test"
PHOTO_TABLE = "rebom_silver.silver_animal_image_auto_b_test"
HISTORY_TABLE = "rebom_silver.silver_animal_image_version_history_auto_b_test"

PHOTO_LOCATION = (
    f"s3://{BUCKET}/bench_tmp/b-storage-test/"
    "iceberg/silver_animal_image_auto_b_test"
)

TARGET = "446482202500677"
SOURCE_RUN = "20261111T225016-5d6934"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    session = boto3.Session(region_name=REGION)

    if session.client("sts").get_caller_identity()["Account"] != ACCOUNT:
        raise RuntimeError("UNEXPECTED_AWS_ACCOUNT")

    s3 = session.client("s3")
    catalog = get_aws_catalog(region=REGION)

    notice_rows = (
        catalog.load_table(NOTICE_TABLE)
        .scan(row_filter=f"desertion_no = '{TARGET}'")
        .to_arrow()
        .to_pylist()
    )

    if len(notice_rows) != 1:
        raise RuntimeError("NOTICE_COUNT_MISMATCH")

    if notice_rows[0]["status_group"] != "PROTECTING":
        raise RuntimeError("NOTICE_NOT_PROTECTING")

    history_rows = (
        catalog.load_table(HISTORY_TABLE)
        .scan(row_filter=f"desertion_no = '{TARGET}'")
        .to_arrow()
        .to_pylist()
    )

    if len(history_rows) != 2:
        raise RuntimeError("HISTORY_COUNT_MISMATCH")

    if {row["image_slot"] for row in history_rows} != {1, 2}:
        raise RuntimeError("PHOTO_SLOT_MISMATCH")

    for row in history_rows:
        if (
            row["version"] != 1
            or row["status"] != "stored"
            or row["run_id"] != SOURCE_RUN
        ):
            raise RuntimeError("HISTORY_VALIDATION_FAILED")

        head = s3.head_object(
            Bucket=BUCKET,
            Key=row["original_s3_key"],
        )

        if head.get("Metadata", {}).get("sha256") != row["sha256"]:
            raise RuntimeError("SHA256_MISMATCH")

        for field in ("serving_s3_key", "thumb_s3_key"):
            s3.head_object(Bucket=BUCKET, Key=row[field])

    print("NOTICE_VALIDATION: PASS")
    print("HISTORY_VALIDATION: PASS")
    print("S3_VALIDATION: PASS")

    table = catalog.load_table(PHOTO_TABLE)
    snapshot = table.current_snapshot()

    print("BEFORE_SNAPSHOT:", snapshot.snapshot_id)

    result = apply_photo_current_updates(
        catalog=catalog,
        table_name=PHOTO_TABLE,
        history_rows=history_rows,
        expected_location=PHOTO_LOCATION,
        apply=False,
        hold_missing_previous=True,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if result["update_count"] != 0 or result["held_slots"]:
        raise RuntimeError("UNSAFE_RECOVERY_PLAN")

    if result["insert_count"] == 0:
        print("RECOVERY_ALREADY_COMPLETED")
        print("AWS_WRITE: False")
        return

    if result["insert_count"] != 2:
        raise RuntimeError("UNEXPECTED_INSERT_COUNT")

    print("RECOVERY_DRY_RUN: PASS")

    if not args.apply:
        print("AWS_WRITE: False")
        return

    latest = catalog.load_table(PHOTO_TABLE).current_snapshot()

    if latest.snapshot_id != snapshot.snapshot_id:
        raise RuntimeError("SNAPSHOT_CHANGED")

    result = apply_photo_current_updates(
        catalog=catalog,
        table_name=PHOTO_TABLE,
        history_rows=history_rows,
        expected_location=PHOTO_LOCATION,
        apply=True,
        hold_missing_previous=True,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))

    after = catalog.load_table(PHOTO_TABLE).current_snapshot()
    print("AFTER_SNAPSHOT:", after.snapshot_id)

    if (
        result["insert_count"] != 2
        or result["update_count"] != 0
        or result["result"] != "APPLIED_AND_VERIFIED"
    ):
        raise RuntimeError("RECOVERY_RESULT_MISMATCH")

    print("RECOVERY_APPLY: PASS")



if __name__ == "__main__":
    main()
