"""Check Silver notice/photo readiness for Gold publication."""

import argparse
import json

import boto3
from botocore.exceptions import ClientError

from rebom.storage.silver.aws_catalog import get_aws_catalog
from rebom.storage.silver.publish_log import (
    assess_publish_readiness,
    build_publish_record,
    save_publish_record_s3,
    find_completed_notice_snapshot_id,
    verify_photo_bronze_run,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bronze-run-id", required=True)
    parser.add_argument("--photo-run-id", required=True)
    parser.add_argument("--notice-table", required=True)
    parser.add_argument("--photo-table", required=True)
    parser.add_argument(
        "--bucket", default="rebom-data-559987919299"
    )
    parser.add_argument(
        "--notice-prefix", default="silver/rebom/journal"
    )
    parser.add_argument(
        "--photo-prefix",
        default="bench_tmp/b-storage-test/journals/photo_auto_runs",
    )
    parser.add_argument("--profile", default="rebom-seoul")
    parser.add_argument("--publish-id")
    parser.add_argument("--publish-prefix", default="silver/rebom/publish")
    parser.add_argument("--publish", action="store_true")

    args = parser.parse_args()

    session = boto3.Session(
        profile_name=args.profile,
        region_name="ap-northeast-2",
    )
    s3 = session.client("s3")
    if args.publish:
        import re

        if not args.publish_id or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", args.publish_id
        ):
            raise ValueError("INVALID_PUBLISH_ID")

        key = (
            f"{args.publish_prefix.rstrip('/')}/"
            f"{args.publish_id}.json"
        )

        try:
            existing = json.loads(
                s3.get_object(
                    Bucket=args.bucket,
                    Key=key,
                )["Body"].read()
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "NoSuchKey":
                raise
        else:
            expected = {
                "publish_id": args.publish_id,
                "bronze_run_id": args.bronze_run_id,
                "notice_run_id": args.bronze_run_id,
                "photo_run_id": args.photo_run_id,
                "notice_table": args.notice_table,
                "photo_table": args.photo_table,
                "status": "READY",
            }

            if any(
                existing.get(k) != v
                for k, v in expected.items()
            ):
                raise RuntimeError("PUBLISH_RECORD_CONFLICT")

            for field in (
                "notice_snapshot_id",
                "photo_snapshot_id",
            ):
                value = existing.get(field)
                if type(value) is not int or value <= 0:
                    raise RuntimeError("INVALID_EXISTING_SNAPSHOT")

            if not existing.get("published_at"):
                raise RuntimeError("PUBLISHED_AT_MISSING")

            print(json.dumps(
                existing, ensure_ascii=False, indent=2
            ))
            print("PUBLISH_RECORD: REUSED")
            return

    catalog = get_aws_catalog(profile=args.profile)

    reasons = []
    notice_snapshot_id = None
    photo_snapshot_id = None
    notice_completed = False
    photo_completed = False
    same_bronze_run = False
    photo_needs_review = True

    notice = catalog.load_table(args.notice_table)
    photo = catalog.load_table(args.photo_table)

    try:
        notice_snapshot_id = find_completed_notice_snapshot_id(
            s3,
            args.bucket,
            args.notice_prefix,
            args.bronze_run_id,
            notice,
        )
        notice_completed = True
    except (RuntimeError, ClientError) as exc:
        reasons.append(f"NOTICE_EVIDENCE_ERROR: {exc}")

    photo_key = (
        f"{args.photo_prefix.rstrip('/')}/"
        f"{args.photo_run_id}.json"
    )

    try:
        response = s3.get_object(
            Bucket=args.bucket,
            Key=photo_key,
        )
        journal = json.loads(response["Body"].read())

        if journal.get("run_id") != args.photo_run_id:
            raise RuntimeError("PHOTO_RUN_ID_MISMATCH")

        same_bronze_run = verify_photo_bronze_run(
            s3,
            args.bucket,
            journal,
            args.bronze_run_id,
        )

        photo_needs_review = journal.get("needs_review")
        photo_snapshot_id = journal.get("current_snapshot_id")

        snapshots = {
            snapshot.snapshot_id
            for snapshot in photo.metadata.snapshots or []
        }

        photo_completed = (
            journal.get("processing_status") == "APPLIED"
            and type(photo_snapshot_id) is int
            and photo_snapshot_id in snapshots
        )

    except (RuntimeError, ClientError, KeyError, ValueError) as exc:
        reasons.append(f"PHOTO_EVIDENCE_ERROR: {exc}")

    status, policy_reasons = assess_publish_readiness(
        notice_completed=notice_completed,
        photo_completed=photo_completed,
        notice_snapshot_id=notice_snapshot_id,
        photo_snapshot_id=photo_snapshot_id,
        same_bronze_run=same_bronze_run,
        photo_needs_review=photo_needs_review,
    )

    result = {
        "bronze_run_id": args.bronze_run_id,
        "photo_run_id": args.photo_run_id,
        "notice_snapshot_id": notice_snapshot_id,
        "photo_snapshot_id": photo_snapshot_id,
        "status": status,
        "reasons": reasons + policy_reasons,
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if status != "READY":
        raise SystemExit(2)

    if args.publish:
        if not args.publish_id:
            raise ValueError("PUBLISH_ID_REQUIRED")

        from datetime import datetime

        bronze_date = args.bronze_run_id[:8]
        manifest_key = (
            f"bronze/rescue/raw/dt={bronze_date[:4]}-"
            f"{bronze_date[4:6]}-{bronze_date[6:8]}/"
            f"run_id={args.bronze_run_id}/manifest.json"
        )

        manifest = json.loads(
            s3.get_object(
                Bucket=args.bucket,
                Key=manifest_key,
            )["Body"].read()
        )

        source_fetched_at = manifest.get("fetched_at")

        if (
            manifest.get("run_id") != args.bronze_run_id
            or manifest.get("status") != "ok"
            or not isinstance(source_fetched_at, str)
        ):
            raise RuntimeError("BRONZE_SOURCE_EVIDENCE_INVALID")

        fetched = datetime.fromisoformat(source_fetched_at)

        if (
            fetched.utcoffset() is None
            or fetched.strftime("%Y%m%dT%H%M%S")
            != args.bronze_run_id[:15]
        ):
            raise RuntimeError("BRONZE_SOURCE_TIME_MISMATCH")

        record = build_publish_record(
            publish_id=args.publish_id,
            bronze_run_id=args.bronze_run_id,
            notice_run_id=args.bronze_run_id,
            photo_run_id=args.photo_run_id,
            notice_table=args.notice_table,
            photo_table=args.photo_table,
            notice_snapshot_id=notice_snapshot_id,
            photo_snapshot_id=photo_snapshot_id,
            status=status,
            notice_completed=notice_completed,
            photo_completed=photo_completed,
            same_bronze_run=same_bronze_run,
            photo_needs_review=photo_needs_review,
            source_fetched_at=source_fetched_at,
        )

        saved = save_publish_record_s3(
            s3, args.bucket, args.publish_prefix, record
        )
        print("PUBLISH_RECORD:", saved)
        print("PUBLISH_ID:", args.publish_id)
    else:
        print("DRY_RUN: NO_PUBLISH_RECORD_WRITTEN")



if __name__ == "__main__":
    main()
