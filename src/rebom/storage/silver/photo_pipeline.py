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
    history_table_name=None,
    expected_history_location=None,
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

    if existing is None and history_table_name is not None:
        return _process_photo_manifest_versioned(
            s3=s3,
            catalog=catalog,
            bucket=bucket,
            manifest=manifest,
            manifest_key=manifest_key,
            table_name=table_name,
            expected_location=expected_location,
            history_table_name=history_table_name,
            expected_history_location=expected_history_location,
            journal_key=journal_key,
            decision=decision,
            apply=apply,
        )

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

        table = catalog.load_table(table_name)

        if table.location().rstrip("/") != expected_location.rstrip("/"):
            raise RuntimeError("Iceberg 테이블 저장 위치 불일치")

        current_rows = _index_rows(
            table.scan().to_arrow().to_pylist()
        )

        if history_table_name is not None:
            if not expected_history_location:
                raise ValueError("사진 이력 테이블 위치 누락")

            from rebom.storage.silver.photo_version_history import (
                select_current_photo_version,
            )

            history_table = catalog.load_table(history_table_name)

            if (
                history_table.location().rstrip("/")
                != expected_history_location.rstrip("/")
            ):
                raise RuntimeError("사진 이력 테이블 저장 위치 불일치")

            history_rows = history_table.scan().to_arrow().to_pylist()
            latest_rows = select_current_photo_version(history_rows)

            for photo in manifest["rows"]:
                key = (photo["desertion_no"], photo["image_slot"])
                current = current_rows.get(key)
                latest = latest_rows.get(key)

                if current is None:
                    raise RuntimeError(
                        f"Journal 존재하지만 Iceberg 행 누락: {key}"
                    )

                if latest is None:
                    raise RuntimeError(
                        f"Journal 사진 버전 이력 누락: {key}"
                    )

                for field in PHOTO_FIELDS:
                    if current.get(field) != latest.get(field):
                        raise RuntimeError(
                            f"현재 사진과 최신 버전 이력 불일치: {key}, {field}"
                        )

                matched = [
                    row
                    for row in history_rows
                    if (
                        row.get("desertion_no") == key[0]
                        and row.get("image_slot") == key[1]
                        and row.get("status") == "stored"
                        and all(
                            row.get(field) == photo.get(field)
                            for field in PHOTO_FIELDS
                        )
                        and (
                            photo.get("version") is None
                            or row.get("version") == photo["version"]
                        )
                    )
                ]

                if not matched:
                    raise RuntimeError(
                        f"Journal 사진 버전 이력 불일치: {key}"
                    )

            return {
                "run_id": run_id,
                "result": "ALREADY_PROCESSED",
                "applied_count": existing["applied_count"],
                "iceberg_verified": True,
            }

        for photo in manifest["rows"]:
            key = (photo["desertion_no"], photo["image_slot"])
            current = current_rows.get(key)

            if current is None:
                raise RuntimeError(
                    f"Journal 존재하지만 Iceberg 행 누락: {key}"
                )

            for field in PHOTO_FIELDS:
                if current.get(field) != photo[field]:
                    raise RuntimeError(
                        f"Journal과 Iceberg 데이터 불일치: {key}, {field}"
                    )

        return {
            "run_id": run_id,
            "result": "ALREADY_PROCESSED",
            "applied_count": existing["applied_count"],
            "iceberg_verified": True,
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


def _process_photo_manifest_versioned(
    s3,
    catalog,
    bucket,
    manifest,
    manifest_key,
    table_name,
    expected_location,
    history_table_name,
    expected_history_location,
    journal_key,
    decision,
    apply=False,
):
    """버전 이력과 현재 사진을 순서대로 반영한다."""
    from rebom.storage.silver.photo_version_history import (
        apply_photo_current_updates,
        apply_photo_version_history,
        build_photo_version_history_rows,
        plan_photo_history_inserts,
    )

    if not expected_history_location:
        raise ValueError("사진 이력 테이블 위치 누락")

    history_rows = build_photo_version_history_rows(manifest)

    for photo in manifest["rows"]:
        key = (photo["desertion_no"], photo["image_slot"])

        head = s3.head_object(
            Bucket=bucket,
            Key=photo["original_s3_key"],
        )

        if (
            head.get("Metadata", {}).get("sha256", "").lower()
            != photo["sha256"]
        ):
            raise RuntimeError(f"사진 해시 메타데이터 불일치: {key}")

    history_plan = apply_photo_version_history(
        catalog,
        history_table_name,
        history_rows,
        expected_history_location,
        apply=False,
    )

    existing_history = (
        catalog.load_table(history_table_name)
        .scan()
        .to_arrow()
        .to_pylist()
    )

    history_changes = plan_photo_history_inserts(
        history_rows,
        existing_history,
    )

    updated_keys = {
        (row["desertion_no"], row["image_slot"], row["version"])
        for row in history_changes["update_rows"]
    }
    projected_history = [
        row for row in existing_history
        if (row["desertion_no"], row["image_slot"], row["version"])
        not in updated_keys
    ]
    projected_history.extend(history_changes["insert_rows"])
    projected_history.extend(history_changes["update_rows"])

    current_plan = apply_photo_current_updates(
        catalog,
        table_name,
        projected_history,
        expected_location,
        apply=False,
    )

    result = {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "decision": decision["decision"],
        "manifest_files": len(manifest["rows"]),
        "failed_count": decision["failed_count"],
        "history_insert_count": history_plan["insert_count"],
        "history_update_count": history_plan["update_count"],
        "changed_rows": current_plan["changed_count"],
    }

    if not apply:
        result["result"] = "DRY_RUN_PASS"
        return result

    history_write = apply_photo_version_history(
        catalog,
        history_table_name,
        history_rows,
        expected_history_location,
        apply=True,
    )

    stored_history = (
        catalog.load_table(history_table_name)
        .scan()
        .to_arrow()
        .to_pylist()
    )

    current_write = apply_photo_current_updates(
        catalog,
        table_name,
        stored_history,
        expected_location,
        apply=True,
    )

    journal = make_photo_journal(
        run_id=manifest["run_id"],
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

    result["history_inserted"] = history_write["rows_inserted"]
    result["history_updated"] = history_write["rows_updated"]
    result["current_changed"] = current_write["changed_count"]
    result["journal_result"] = journal_result
    result["result"] = "APPLIED_AND_VERIFIED"

    return result
