"""사진 Manifest를 Silver 사진 버전 이력 행으로 변환한다."""


def build_photo_version_history_rows(manifest):
    run_id = manifest.get("run_id")

    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("사진 이력 run_id 누락")

    rows = manifest.get("rows", [])
    failed_rows = manifest.get("failed_rows", [])

    if not isinstance(rows, list) or not isinstance(failed_rows, list):
        raise ValueError("사진 이력 입력 형식 오류")

    result = []
    seen = set()

    for status, items in (
        ("stored", rows),
        ("failed", failed_rows),
    ):
        for item in items:
            no = item.get("desertion_no")
            slot = item.get("image_slot")
            version = item.get("version", 1)
            replaces = item.get("replaces")

            if not isinstance(no, str) or not no.strip():
                raise ValueError("사진 이력 공고번호 오류")

            if type(slot) is not int or not 1 <= slot <= 8:
                raise ValueError("사진 이력 슬롯 오류")

            if type(version) is not int or version < 1:
                raise ValueError("사진 이력 버전 오류")

            if version == 1:
                if replaces is not None:
                    raise ValueError("사진 v1 이전 버전 오류")
            elif type(replaces) is not int or replaces != version - 1:
                raise ValueError("사진 이력 이전 버전 불일치")

            key = (no, slot, version)

            if key in seen:
                raise ValueError(f"사진 이력 중복 버전: {key}")

            seen.add(key)

            if status == "stored":
                if not item.get("original_s3_key") or not item.get("sha256"):
                    raise ValueError("정상 사진 저장 정보 누락")
                failure_reason = None
            else:
                failure_reason = item.get("reason")
                if failure_reason != "replaced_not_found":
                    raise ValueError("지원하지 않는 사진 교체 실패 사유")

            result.append({
                "desertion_no": no,
                "image_slot": slot,
                "version": version,
                "replaces": replaces,
                "first_seen_at": item.get("first_seen_at"),
                "source_url": item.get("source_url"),
                "original_s3_key": (
                    item.get("original_s3_key")
                    if status == "stored" else None
                ),
                "serving_s3_key": (
                    item.get("serving_s3_key")
                    if status == "stored" else None
                ),
                "thumb_s3_key": (
                    item.get("thumb_s3_key")
                    if status == "stored" else None
                ),
                "sha256": (
                    item.get("sha256")
                    if status == "stored" else None
                ),
                "status": status,
                "failure_reason": failure_reason,
                "run_id": run_id,
            })

    return sorted(
        result,
        key=lambda row: (
            row["desertion_no"],
            row["image_slot"],
            row["version"],
        ),
    )

def select_current_photo_version(history_rows):
    """공고번호와 사진 슬롯별 최신 정상 저장 버전을 선택한다."""
    current = {}

    for row in history_rows:
        status = row.get("status")

        if status not in {"stored", "failed"}:
            raise ValueError(f"알 수 없는 사진 이력 상태: {status}")

        if status == "failed":
            continue

        no = row.get("desertion_no")
        slot = row.get("image_slot")
        version = row.get("version")

        if not isinstance(no, str) or not no.strip():
            raise ValueError("현재 사진 공고번호 오류")

        if type(slot) is not int or not 1 <= slot <= 8:
            raise ValueError("현재 사진 슬롯 오류")

        if type(version) is not int or version < 1:
            raise ValueError("현재 사진 버전 오류")

        key = (no, slot)
        previous = current.get(key)

        if previous is None or version > previous["version"]:
            current[key] = row.copy()
        elif version == previous["version"] and row != previous:
            raise ValueError(f"같은 사진 버전 데이터 충돌: {key}")

    return current


def plan_photo_history_inserts(incoming_rows, existing_rows):
    """기존 사진 버전을 보호하면서 신규 이력 INSERT 대상을 결정한다."""
    keys = ("desertion_no", "image_slot", "version")

    def make_index(rows, label):
        if not isinstance(rows, list):
            raise ValueError(f"{label} 사진 이력 형식 오류")

        indexed = {}

        for row in rows:
            key = tuple(row.get(field) for field in keys)

            if (
                not isinstance(key[0], str)
                or not key[0].strip()
                or type(key[1]) is not int
                or not 1 <= key[1] <= 8
                or type(key[2]) is not int
                or key[2] < 1
            ):
                raise ValueError(f"{label} 사진 이력 키 오류: {key}")

            if key in indexed:
                raise ValueError(f"{label} 사진 이력 중복 키: {key}")

            indexed[key] = row

        return indexed

    incoming = make_index(incoming_rows, "입력")
    existing = make_index(existing_rows, "기존")

    insert_rows = []
    skip_count = 0

    for key, row in incoming.items():
        previous = existing.get(key)

        if previous is None:
            insert_rows.append(row.copy())
            continue

        incoming_data = {
            field: value
            for field, value in row.items()
            if field != "run_id"
        }
        existing_data = {
            field: value
            for field, value in previous.items()
            if field != "run_id"
        }

        if incoming_data != existing_data:
            raise ValueError(f"사진 이력 데이터 충돌: {key}")

        skip_count += 1

    return {
        "insert_rows": insert_rows,
        "insert_count": len(insert_rows),
        "skip_count": skip_count,
    }

def apply_photo_version_history(
    catalog,
    table_name,
    incoming_rows,
    expected_location,
    apply=False,
):
    """사진 버전 이력을 중복 및 충돌 검사 후 Iceberg에 저장한다."""
    import pyarrow as pa

    keys = ("desertion_no", "image_slot", "version")
    table = catalog.load_table(table_name)

    if table.location().rstrip("/") != expected_location.rstrip("/"):
        raise RuntimeError("사진 이력 Iceberg 저장 위치 불일치")

    schema = table.schema().as_arrow()
    existing_rows = table.scan().to_arrow().to_pylist()

    plan = plan_photo_history_inserts(incoming_rows, existing_rows)
    insert_rows = plan["insert_rows"]

    for row in insert_rows:
        extra = set(row) - set(schema.names)

        if extra:
            raise ValueError(f"사진 이력 예상 외 컬럼: {sorted(extra)}")

        missing = [
            field.name
            for field in schema
            if not field.nullable and row.get(field.name) is None
        ]

        if missing:
            raise ValueError(f"사진 이력 필수 컬럼 누락: {missing}")

    result = {
        "insert_count": plan["insert_count"],
        "skip_count": plan["skip_count"],
        "mode": "APPLY" if apply else "DRY_RUN",
    }

    if not apply:
        result["result"] = "DRY_RUN_PASS"
        return result

    if insert_rows:
        arrow = pa.Table.from_pylist(insert_rows, schema=schema)

        write = table.upsert(
            arrow,
            join_cols=list(keys),
            when_matched_update_all=True,
            when_not_matched_insert_all=True,
        )

        result["rows_inserted"] = write.rows_inserted
        result["rows_updated"] = write.rows_updated

        if write.rows_updated != 0:
            raise RuntimeError("사진 이력 기존 버전 변경 감지")
    else:
        result["rows_inserted"] = 0
        result["rows_updated"] = 0

    stored_rows = catalog.load_table(
        table_name
    ).scan().to_arrow().to_pylist()

    verification = plan_photo_history_inserts(
        incoming_rows,
        stored_rows,
    )

    if verification["insert_count"] != 0:
        raise RuntimeError("사진 이력 Iceberg 적재 검증 실패")

    result["verified_rows"] = len(stored_rows)
    result["result"] = "APPLIED_AND_VERIFIED"

    return result

def plan_photo_current_updates(current_rows, history_rows):
    """사진 이력을 기준으로 현재 사진 테이블의 변경사항을 계산한다."""
    fields = (
        "original_s3_key",
        "serving_s3_key",
        "thumb_s3_key",
        "sha256",
    )

    current = {}

    for row in current_rows:
        key = (row["desertion_no"], row["image_slot"])

        if key in current:
            raise ValueError(f"현재 사진 키 중복: {key}")

        current[key] = row

    latest = select_current_photo_version(history_rows)
    updates = []

    for key, selected in latest.items():
        if key not in current:
            raise RuntimeError(f"현재 사진 원본 누락: {key}")

        before = current[key]

        if (
            all(before.get(field) == selected.get(field) for field in fields)
            and (
                "source_url" not in before
                or before.get("source_url") == selected.get("source_url")
            )
        ):
            continue

        known_versions = [
            row
            for row in history_rows
            if (
                (row.get("desertion_no"), row.get("image_slot")) == key
                and row.get("status") == "stored"
                and all(
                    before.get(field) == row.get(field)
                    for field in fields
                )
            )
        ]

        is_empty_slot = all(before.get(field) is None for field in fields)

        if not is_empty_slot and not known_versions:
            raise RuntimeError(f"현재 사진과 저장 이력 불일치: {key}")

        merged = before.copy()

        for field in fields:
            merged[field] = selected.get(field)

        if "source_url" in before:
            merged["source_url"] = selected.get("source_url")

        updates.append(merged)

    return {
        "updates": updates,
        "changed_count": len(updates),
    }


def apply_photo_current_updates(
    catalog,
    table_name,
    history_rows,
    expected_location,
    apply=False,
):
    """버전 이력을 기준으로 현재 사진을 Iceberg에 반영한다."""
    import pyarrow as pa
    from functools import reduce
    from pyiceberg.expressions import And, EqualTo, Or

    table = catalog.load_table(table_name)

    if table.location().rstrip("/") != expected_location.rstrip("/"):
        raise RuntimeError("현재 사진 Iceberg 저장 위치 불일치")

    schema = table.schema().as_arrow()
    before_rows = table.scan().to_arrow().to_pylist()

    plan = plan_photo_current_updates(before_rows, history_rows)
    updates = plan["updates"]

    result = {
        "changed_count": len(updates),
        "mode": "APPLY" if apply else "DRY_RUN",
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

        table.overwrite(
            pa.Table.from_pylist(updates, schema=schema),
            overwrite_filter=reduce(Or, filters),
        )

    expected = {
        (row["desertion_no"], row["image_slot"]): row
        for row in before_rows
    }

    for row in updates:
        expected[(row["desertion_no"], row["image_slot"])] = row

    after_rows = (
        catalog.load_table(table_name)
        .scan()
        .to_arrow()
        .to_pylist()
    )

    actual = {
        (row["desertion_no"], row["image_slot"]): row
        for row in after_rows
    }

    if actual != expected:
        raise RuntimeError("현재 사진 Iceberg 반영 검증 실패")

    result["verified_rows"] = len(after_rows)
    result["result"] = "APPLIED_AND_VERIFIED"

    return result
