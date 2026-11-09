"""A팀 사진 Manifest를 B팀 Silver Iceberg에 반영한다."""

import json
import tempfile
from functools import reduce
from pathlib import Path

import pyarrow as pa
from pyiceberg.expressions import And, EqualTo, Or

from rebom.storage.silver.load_photo_manifest import load_photo_manifest
from rebom.storage.silver.photo_manifest_policy import decide_photo_manifest
from rebom.storage.silver.photo_processing_journal import (
    load_photo_journal_s3,
    make_photo_journal,
    save_photo_journal_s3,
)

PHOTO_FIELDS = (
    "original_s3_key",
    "serving_s3_key",
    "thumb_s3_key",
    "sha256",
)


def _index_rows(rows):
    result = {}
    for row in rows:
        key = (row["desertion_no"], row["image_slot"])
        if key in result:
            raise RuntimeError(f"이미지 키 중복: {key}")
        result[key] = row
    return result


def process_photo_manifest(
    s3,
    catalog,
    bucket,
    manifest_key,
    table_name,
    expected_location,
    journal_prefix,
    apply=False,
):
    if not (
        manifest_key.startswith("bronze/rescue/image_runs/")
        and manifest_key.endswith("/manifest.json")
    ):
        raise ValueError("사진 Manifest S3 경로 오류")

    raw = s3.get_object(
        Bucket=bucket,
        Key=manifest_key,
    )["Body"].read()

    source = json.loads(raw)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "manifest.json"
        path.write_bytes(raw)
        manifest = load_photo_manifest(path)

    decision = decide_photo_manifest(source)
    run_id = manifest["run_id"]

    if f"/run_id={run_id}/manifest.json" not in manifest_key:
        raise ValueError("Manifest 경로와 run_id 불일치")

    if decision["decision"] == "DEFER":
        return {
            "run_id": run_id,
            "result": "DEFERRED",
            "status": manifest["status"],
        }

    journal_key = f"{journal_prefix.rstrip('/')}/{run_id}.json"
    existing = load_photo_journal_s3(s3, bucket, journal_key)

    if existing is not None:
        expected_status = (
            "APPLIED"
            if manifest["status"] == "ok"
            else "APPLIED_PARTIAL"
        )

        checks = {
            "run_id": run_id,
            "manifest_status": manifest["status"],
            "processing_status": expected_status,
            "applied_count": len(manifest["rows"]),
        }

        for field, expected in checks.items():
            if existing.get(field) != expected:
                raise RuntimeError(
                    f"기존 Journal 불일치: {field}"
                )

        if (
            existing.get("manifest_key") is not None
            and existing["manifest_key"] != manifest_key
        ):
            raise RuntimeError("기존 Journal Manifest 경로 불일치")

        return {
            "run_id": run_id,
            "result": "ALREADY_PROCESSED",
            "applied_count": existing["applied_count"],
        }

    table = catalog.load_table(table_name)

    if table.location().rstrip("/") != expected_location.rstrip("/"):
        raise RuntimeError("Iceberg 테이블 저장 위치 불일치")

    schema = table.schema().as_arrow()

    if not set(PHOTO_FIELDS).issubset(schema.names):
        raise RuntimeError("Iceberg 사진 컬럼 누락")

    before = _index_rows(
        table.scan().to_arrow().to_pylist()
    )

    expected = dict(before)
    updates = []

    for photo in manifest["rows"]:
        key = (photo["desertion_no"], photo["image_slot"])

        if key not in before:
            raise RuntimeError(f"Silver 이미지 원본 누락: {key}")

        head = s3.head_object(
            Bucket=bucket,
            Key=photo["original_s3_key"],
        )

        if (
            head.get("Metadata", {}).get("sha256", "").lower()
            != photo["sha256"]
        ):
            raise RuntimeError(f"사진 해시 메타데이터 불일치: {key}")

        original = before[key]
        merged = original.copy()

        if (
            original.get("sha256") not in (None, photo["sha256"])
        ):
            raise RuntimeError(f"기존 사진 해시 충돌: {key}")

        for field in PHOTO_FIELDS:
            merged[field] = photo[field]

        expected[key] = merged

        if merged != original:
            updates.append(merged)

    result = {
        "run_id": run_id,
        "status": manifest["status"],
        "decision": decision["decision"],
        "manifest_files": len(manifest["rows"]),
        "failed_count": decision["failed_count"],
        "changed_rows": len(updates),
        "image_total": len(before),
    }

    if not apply:
        result["result"] = "DRY_RUN_PASS"
        return result

    if updates:
        filters = [
            And(
                EqualTo("desertion_no", row["desertion_no"]),
                EqualTo("image_slot", row["image_slot"]),
            )
            for row in updates
        ]

        overwrite_filter = reduce(Or, filters)

        table.overwrite(
            pa.Table.from_pylist(updates, schema=schema),
            overwrite_filter=overwrite_filter,
        )

    after = _index_rows(
        catalog.load_table(table_name).scan().to_arrow().to_pylist()
    )

    if after != expected:
        raise RuntimeError("Iceberg 반영 후 데이터 검증 실패")

    journal = make_photo_journal(
        run_id=run_id,
        manifest_key=manifest_key,
        manifest_status=manifest["status"],
        applied_count=len(manifest["rows"]),
        needs_review=decision["needs_review"],
        failed_count=decision["failed_count"],
    )

    journal_result = save_photo_journal_s3(
        s3,
        bucket,
        journal_key,
        journal,
    )

    result["result"] = "APPLIED_AND_VERIFIED"
    result["journal_result"] = journal_result

    return result
