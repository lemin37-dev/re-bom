"""사진 Manifest 자동 탐색, 정체 감지 및 Silver 처리."""

import argparse
import json
import os

import boto3

from rebom.storage.silver.aws_catalog import get_aws_catalog
from rebom.storage.silver.photo_pipeline import process_photo_manifest
from rebom.storage.silver.photo_staleness import evaluate_photo_staleness


EXPECTED_ACCOUNT = "559987919299"
EXPECTED_REGION = "ap-northeast-2"
EXPECTED_BUCKET = "rebom-data-559987919299"
TEST_PREFIX = "bench_tmp/b-storage-test/"


def get_config():
    bucket = os.getenv("REBOM_S3_BUCKET", EXPECTED_BUCKET)
    region = os.getenv("AWS_DEFAULT_REGION", EXPECTED_REGION)
    profile = os.getenv("AWS_PROFILE", "rebom-seoul")

    table_name = os.getenv(
        "REBOM_PHOTO_ICEBERG_TABLE",
        "rebom_silver.silver_animal_image_b_test",
    )

    journal_prefix = os.getenv(
        "REBOM_PHOTO_JOURNAL_PREFIX",
        "bench_tmp/b-storage-test/journals/photo_runs",
    ).rstrip("/")

    manifest_prefix = os.getenv(
        "REBOM_PHOTO_MANIFEST_PREFIX",
        "bronze/rescue/image_runs/",
    )

    expected_location = os.getenv(
        "REBOM_PHOTO_ICEBERG_LOCATION",
        f"s3://{bucket}/bench_tmp/b-storage-test/"
        "iceberg/silver_animal_image_b_test",
    ).rstrip("/")

    stale_seconds = int(
        os.getenv("REBOM_PHOTO_STALE_SECONDS", "600")
    )

    return {
        "bucket": bucket,
        "region": region,
        "profile": profile,
        "table_name": table_name,
        "journal_prefix": journal_prefix,
        "manifest_prefix": manifest_prefix,
        "expected_location": expected_location,
        "stale_seconds": stale_seconds,
    }


def validate_config(config):
    if config["region"] != EXPECTED_REGION:
        raise RuntimeError("허용되지 않은 AWS 리전")

    if config["bucket"] != EXPECTED_BUCKET:
        raise RuntimeError("허용되지 않은 S3 버킷")

    if not (
        config["table_name"].startswith("rebom_silver.")
        and config["table_name"].endswith("_b_test")
    ):
        raise RuntimeError("테스트 전용 Iceberg 테이블만 허용합니다")

    if not config["journal_prefix"].startswith(TEST_PREFIX):
        raise RuntimeError("Journal은 테스트 영역에만 기록할 수 있습니다")

    if not config["expected_location"].startswith(
        f"s3://{EXPECTED_BUCKET}/{TEST_PREFIX}"
    ):
        raise RuntimeError("Iceberg 저장 위치가 테스트 영역이 아닙니다")

    if not config["manifest_prefix"].startswith(
        "bronze/rescue/image_runs/"
    ):
        raise RuntimeError("사진 Manifest 경로 오류")

    if config["stale_seconds"] <= 0:
        raise RuntimeError("정체 판단 기준은 양수여야 합니다")

    expected_table = config["table_name"].split(".", 1)[1]
    expected_path = (
        f"s3://{EXPECTED_BUCKET}/{TEST_PREFIX}"
        f"iceberg/{expected_table}"
    )

    if config["expected_location"] != expected_path:
        raise RuntimeError("Iceberg 테이블 이름과 저장 위치 불일치")


def find_manifests(s3, bucket, prefix):
    keys = []
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(
        Bucket=bucket,
        Prefix=prefix,
    ):
        for item in page.get("Contents", []):
            if item["Key"].endswith("/manifest.json"):
                keys.append(item["Key"])

    return sorted(keys)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()

    config = get_config()
    validate_config(config)

    print("CONFIG_VALIDATION: PASS")
    print("REGION:", config["region"])
    print("BUCKET:", config["bucket"])
    print("TABLE:", config["table_name"])
    print("JOURNAL_PREFIX:", config["journal_prefix"])
    print("STALE_SECONDS:", config["stale_seconds"])

    if args.check_config:
        print("AWS_ACCESS: False")
        return

    session = boto3.Session(
        profile_name=config["profile"],
        region_name=config["region"],
    )

    sts = session.client("sts")
    account = sts.get_caller_identity()["Account"]

    if account != EXPECTED_ACCOUNT:
        raise RuntimeError(
            f"잘못된 AWS 계정: {account}"
        )

    print("AWS_ACCOUNT_VALIDATION: PASS")

    s3 = session.client("s3")
    catalog = get_aws_catalog(
        profile=config["profile"],
        region=config["region"],
    )

    keys = find_manifests(
        s3,
        config["bucket"],
        config["manifest_prefix"],
    )

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

    for key in keys:
        stale = evaluate_photo_staleness(
            s3=s3,
            bucket=config["bucket"],
            manifest_key=key,
            threshold_seconds=config["stale_seconds"],
        )

        decision = stale["decision"]
        run_id = stale["run_id"]

        if decision == "STALE_REVIEW":
            counts["stale_review"] += 1
            print("STALE_REVIEW:", run_id)
            continue

        if decision == "DEFER_INVALID_TIMESTAMP":
            counts["invalid_timestamp"] += 1
            print("INVALID_TIMESTAMP:", run_id)
            continue

        if decision == "DEFER":
            counts["deferred"] += 1
            print("DEFER:", run_id)
            continue

        if decision != "READY_FINAL":
            raise RuntimeError(
                f"예상하지 못한 상태: {stale}"
            )

        result = process_photo_manifest(
            s3=s3,
            catalog=catalog,
            bucket=config["bucket"],
            manifest_key=key,
            table_name=config["table_name"],
            expected_location=config["expected_location"],
            journal_prefix=config["journal_prefix"],
            apply=args.apply,
        )

        state = result["result"]

        mapping = {
            "ALREADY_PROCESSED": "already_processed",
            "DRY_RUN_PASS": "ready",
            "APPLIED_AND_VERIFIED": "applied",
            "DEFERRED": "deferred",
        }

        if state not in mapping:
            raise RuntimeError(f"알 수 없는 처리 결과: {result}")

        counts[mapping[state]] += 1

        print("RUN:", run_id, json.dumps(result, ensure_ascii=False))

    print("SUMMARY:", json.dumps(counts, ensure_ascii=False))
    print("AUTO_PHOTO_PIPELINE: PASS")


if __name__ == "__main__":
    main()
