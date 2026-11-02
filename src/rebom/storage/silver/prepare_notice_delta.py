"""검증된 Bronze 실행본에서 안전한 Silver 공고 증분을 준비한다."""

import json

from rebom.storage.silver.transform_notice import transform_notice


NOTICE_KINDS = ("updated", "list_notice", "list_protect")
EXCLUDED_RAW_FIELDS = {"updTm"}
EXCLUDED_SILVER_FIELDS = {"source_updated_at_raw"}


def content_signature(row, excluded):
    # JSON 필드 순서가 달라도 실제 값이 같다면 같은 내용으로 판단한다.
    # updTm은 A/B 계약에 따라 변경 판단에서 제외한다.
    filtered = {key: value for key, value in row.items() if key not in excluded}

    return json.dumps(
        filtered,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def index_silver(rows):
    # Iceberg에서 조회한 기존 공고는 desertion_no로 인덱싱한다.
    # 기존 Silver에 중복 키가 있으면 증분 처리 전에 중단한다.
    indexed = {}

    for row in rows:
        key = str(row.get("desertion_no") or "").strip()

        if not key or key in indexed:
            raise ValueError(f"기존 Silver 공고번호 오류: {key}")

        indexed[key] = row

    return indexed


def prepare_notice_delta(verified_run, existing_silver_rows):
    # verified_run은 load_bronze_run()의 결과만 받는다.
    # 이 함수 자체는 파일 검증이나 Iceberg 쓰기를 하지 않는다.
    #
    # 단계:
    # Bronze 출처별 공고 수집
    # -> 동일 공고 중복 및 충돌 확인
    # -> 기존 Silver 내용과 비교
    # -> INSERT/UPDATE 대상 반환

    rows_by_kind = verified_run["rows_by_kind"]
    grouped = {}
    total_input = 0

    for kind in NOTICE_KINDS:
        for raw in rows_by_kind.get(kind, []):
            total_input += 1

            if not isinstance(raw, dict):
                raise ValueError(f"{kind}: 공고 형식 오류")

            key = str(raw.get("desertionNo") or "").strip()

            if not key:
                raise ValueError(f"{kind}: desertionNo 누락")

            grouped.setdefault(key, []).append((kind, raw))

    # 기존 데이터와 비교하기 전에 Bronze 내부의 충돌부터 확인한다.
    # 출처 우선순위는 아직 확정되지 않았으므로 임의 선택하지 않는다.
    unique = {}
    conflicts = []

    for key, candidates in grouped.items():
        raw_signatures = {
            content_signature(raw, EXCLUDED_RAW_FIELDS)
            for _, raw in candidates
        }

        # 원본 내용이 다르면 상태, 이미지 URL, 특징 문장 등의
        # 차이일 수 있다. 실데이터 검증 전에는 자동 병합하지 않는다.
        if len(raw_signatures) != 1:
            conflicts.append({
                "desertion_no": key,
                "sources": sorted({kind for kind, _ in candidates}),
                "reason": "raw_content_conflict",
            })
            continue

        # 원본은 같더라도 source_type에 따른 보정 결과가 다르면
        # 하나를 임의로 선택하지 않는다.
        transformed = [
            transform_notice(raw, source_type=kind)
            for kind, raw in candidates
        ]

        silver_signatures = {
            content_signature(row, EXCLUDED_SILVER_FIELDS)
            for row in transformed
        }

        if len(silver_signatures) != 1:
            conflicts.append({
                "desertion_no": key,
                "sources": sorted({kind for kind, _ in candidates}),
                "reason": "silver_transform_conflict",
            })
            continue

        unique[key] = transformed[0]

    # 충돌이 한 건이라도 있으면 실행 전체를 적재 대상으로 확정하지 않는다.
    # 원본 값이나 개인정보를 오류 로그에 출력하지 않는다.
    if conflicts:
        return {
            "ready": False,
            "delta_rows": [],
            "insert_count": 0,
            "update_count": 0,
            "unchanged_count": 0,
            "total_input": total_input,
            "unique_count": len(grouped),
            "duplicate_count": total_input - len(grouped),
            "conflicts": conflicts,
        }

    existing = index_silver(existing_silver_rows)

    delta_rows = []
    insert_count = 0
    update_count = 0
    unchanged_count = 0

    for key, silver in unique.items():
        if key not in existing:
            delta_rows.append(silver)
            insert_count += 1
            continue

        old_signature = content_signature(
            existing[key], EXCLUDED_SILVER_FIELDS
        )
        new_signature = content_signature(
            silver, EXCLUDED_SILVER_FIELDS
        )

        if old_signature == new_signature:
            unchanged_count += 1
        else:
            delta_rows.append(silver)
            update_count += 1

    # 삭제나 종료 판정은 이 함수에서 수행하지 않는다.
    # 부분 증분에 없는 공고는 그대로 유지해야 한다.
    return {
        "ready": True,
        "delta_rows": delta_rows,
        "insert_count": insert_count,
        "update_count": update_count,
        "unchanged_count": unchanged_count,
        "total_input": total_input,
        "unique_count": len(grouped),
        "duplicate_count": total_input - len(grouped),
        "conflicts": [],
    }
