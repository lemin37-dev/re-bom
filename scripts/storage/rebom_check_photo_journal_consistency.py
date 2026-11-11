"""사진 Journal과 Iceberg 정합성 읽기 전용 검증."""

import json
import os
import sys

import boto3

from rebom.storage.silver.aws_catalog import get_aws_catalog
from rebom.storage.silver.load_photo_manifest import load_photo_manifest
from rebom.storage.silver.photo_processing_journal import load_photo_journal_s3

import tempfile
from pathlib import Path


ACCOUNT = "559987919299"
REGION = "ap-northeast-2"
BUCKET = "rebom-data-559987919299"
TABLE = "rebom_silver.silver_animal_image_b_test"
LOCATION = (
    f"s3://{BUCKET}/bench_tmp/b-storage-test/"
    "iceberg/silver_animal_image_b_test"
)
JOURNAL_PREFIX = "bench_tmp/b-storage-test/journals/photo_runs/"

FIELDS = (
    "original_s3_key",
    "serving_s3_key",
    "thumb_s3_key",
    "sha256",
)


def check_photo_rows(photos, index):
    mismatches = []
    checked_count = 0

    for photo in photos:
        key = (photo["desertion_no"], photo["image_slot"])
        current = index.get(key)

        if current is None:
            mismatches.append(f"{key}: Iceberg 행 없음")
            continue

        checked_count += 1

        for field in FIELDS:
            if current.get(field) != photo[field]:
                mismatches.append(f"{key}: {field} 불일치")

    return mismatches, checked_count

def main():
    profile = os.getenv("AWS_PROFILE", "rebom-seoul")

    session = boto3.Session(
        profile_name=profile,
        region_name=REGION,
    )

    actual_account = session.client("sts").get_caller_identity()["Account"]

    if actual_account != ACCOUNT:
        raise RuntimeError("AWS 계정 불일치")

    s3 = session.client("s3")
    catalog = get_aws_catalog(profile=profile, region=REGION)
    table = catalog.load_table(TABLE)

    if table.location().rstrip("/") != LOCATION:
        raise RuntimeError("Iceberg 테이블 위치 불일치")

    rows = table.scan().to_arrow().to_pylist()
    index = {}

    for row in rows:
        key = (row["desertion_no"], row["image_slot"])

        if key in index:
            raise RuntimeError(f"Iceberg 키 중복: {key}")

        index[key] = row

    paginator = s3.get_paginator("list_objects_v2")
    journal_keys = []

    for page in paginator.paginate(
        Bucket=BUCKET,
        Prefix=JOURNAL_PREFIX,
    ):
        for item in page.get("Contents", []):
            key = item["Key"]
            if key.endswith(".json"):
                journal_keys.append(key)

    print("MODE: READ_ONLY")
    print("ICEBERG_TOTAL:", len(rows))
    print("JOURNAL_TOTAL:", len(journal_keys))

    checked = 0
    problems = []

    if not journal_keys:
        problems.append("검증할 Journal이 없습니다")

    for journal_key in sorted(journal_keys):
        try:
            journal = load_photo_journal_s3(
                s3, BUCKET, journal_key
            )

            if journal.get("test_fixture") is True:
                print("SKIP_TEST_FIXTURE:", journal_key)
                continue

            run_id = journal["run_id"]
            manifest_key = journal["manifest_key"]

            if journal_key != f"{JOURNAL_PREFIX}{run_id}.json":
                raise RuntimeError("Journal 경로·실행 ID 불일치")

            if not (
                manifest_key.startswith("bronze/rescue/image_runs/")
                and manifest_key.endswith(f"/run_id={run_id}/manifest.json")
            ):
                raise RuntimeError("Manifest 경로 오류")

            raw = s3.get_object(
                Bucket=BUCKET,
                Key=manifest_key,
            )["Body"].read()

            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "manifest.json"
                path.write_bytes(raw)
                manifest = load_photo_manifest(path)

            if manifest["run_id"] != run_id:
                raise RuntimeError("Manifest 실행 ID 불일치")

            expected_status = (
                "APPLIED"
                if manifest["status"] == "ok"
                else "APPLIED_PARTIAL"
            )

            if manifest["status"] not in {"ok", "aborted"}:
                raise RuntimeError("미완료 Manifest Journal")

            if journal["manifest_status"] != manifest["status"]:
                raise RuntimeError("Manifest 상태 불일치")

            if journal["processing_status"] != expected_status:
                raise RuntimeError("Journal 처리 상태 불일치")

            if journal["applied_count"] != len(manifest["rows"]):
                raise RuntimeError("Journal 처리 건수 불일치")

            mismatches, checked_count = check_photo_rows(
                manifest["rows"], index
            )
            checked += checked_count

            if mismatches:
                problems.extend(
                    f"{run_id}: {message}"
                    for message in mismatches
                )
                print("RUN:", run_id, "MISMATCH")
            else:
                print(
                    "RUN:", run_id,
                    "PASS",
                    "ROWS:", len(manifest["rows"]),
                )

        except Exception as exc:
            problems.append(f"{journal_key}: {exc}")
            print("RUN_ERROR:", journal_key, str(exc))

    print("CHECKED_ROWS:", checked)
    print("PROBLEM_COUNT:", len(problems))

    if problems:
        for problem in problems[:20]:
            print("PROBLEM:", problem)

        print("JOURNAL_ICEBERG_CHECK: FAIL")
        sys.exit(1)

    print("JOURNAL_ICEBERG_CHECK: PASS")


if __name__ == "__main__":
    main()
