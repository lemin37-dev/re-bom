"""사진 Manifest 자동 탐색, 정체 감지 및 Silver 처리."""

import argparse
import json
import os

import boto3

from rebom.storage.silver.aws_catalog import get_aws_catalog
from rebom.storage.silver.photo_pipeline import process_photo_manifest
from rebom.storage.silver.photo_staleness import evaluate_photo_staleness

BUCKET = "rebom-data-559987919299"
MANIFEST_PREFIX = "bronze/rescue/image_runs/"
JOURNAL_PREFIX = "bench_tmp/b-storage-test/journals/photo_runs"
TABLE_NAME = "rebom_silver.silver_animal_image_b_test"
EXPECTED_LOCATION = (
    f"s3://{BUCKET}/bench_tmp/b-storage-test/"
    "iceberg/silver_animal_image_b_test"
)


def find_manifests(s3):
    keys = []
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(
        Bucket=BUCKET,
        Prefix=MANIFEST_PREFIX,
    ):
        for item in page.get("Contents", []):
            if item["Key"].endswith("/manifest.json"):
                keys.append(item["Key"])

    return sorted(keys)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    session = boto3.Session(
        profile_name=os.getenv("AWS_PROFILE", "rebom-seoul"),
        region_name=os.getenv("AWS_DEFAULT_REGION", "ap-northeast-2"),
    )

    s3 = session.client("s3")
    catalog = get_aws_catalog()

    keys = find_manifests(s3)

    counts = {
        "already_processed": 0,
        "ready": 0,
        "applied": 0,
        "deferred": 0,
        "stale_review": 0,
        "invalid_timestamp": 0,
    }

    print("MANIFEST_TOTAL:", len(keys))
    print("MODE:", "APPLY" if args.apply else "DRY_RUN")
    print(
        "STALE_SECONDS:",
        os.getenv("REBOM_PHOTO_STALE_SECONDS", "600"),
    )

    for key in keys:
        stale = evaluate_photo_staleness(
            s3=s3,
            bucket=BUCKET,
            manifest_key=key,
        )

        stale_decision = stale["decision"]
        run_id = stale["run_id"]

        if stale_decision == "STALE_REVIEW":
            counts["stale_review"] += 1
            print("STALE_REVIEW:", run_id, stale)
            continue

        if stale_decision == "DEFER_INVALID_TIMESTAMP":
            counts["invalid_timestamp"] += 1
            print("INVALID_TIMESTAMP:", run_id, stale)
            continue

        if stale_decision == "DEFER":
            counts["deferred"] += 1
            print("DEFER:", run_id, stale)
            continue

        if stale_decision != "READY_FINAL":
            raise RuntimeError(
                f"Unexpected staleness decision: {stale}"
            )

        result = process_photo_manifest(
            s3=s3,
            catalog=catalog,
            bucket=BUCKET,
            manifest_key=key,
            table_name=TABLE_NAME,
            expected_location=EXPECTED_LOCATION,
            journal_prefix=JOURNAL_PREFIX,
            apply=args.apply,
        )

        state = result["result"]

        if state == "ALREADY_PROCESSED":
            counts["already_processed"] += 1
        elif state == "DRY_RUN_PASS":
            counts["ready"] += 1
        elif state == "APPLIED_AND_VERIFIED":
            counts["applied"] += 1
        elif state == "DEFERRED":
            counts["deferred"] += 1
        else:
            raise RuntimeError(f"Unexpected result: {result}")

        print(
            "RUN:",
            run_id,
            json.dumps(result, ensure_ascii=False),
        )

    print("SUMMARY:", json.dumps(counts, ensure_ascii=False))
    print("AUTO_PHOTO_PIPELINE: PASS")


if __name__ == "__main__":
    main()
