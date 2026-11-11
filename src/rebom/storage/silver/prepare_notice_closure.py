"""검증된 추정 종료 이력을 Silver 공고의 현재 상태 변경분으로 변환한다.

Iceberg 쓰기는 수행하지 않는다.
"""

from rebom.storage.cleaning.status import parse_status
from rebom.storage.silver.reconstruct_status_snapshot import index_silver


INFERRED_CLOSED_STATE = "\uc885\ub8cc(\uc0ac\uc720 \ubbf8\uc0c1)"


def prepare_notice_closure(previous_silver_rows, reconstructed):
    # 종료 근거 검증과 상태 재구성이 완료된 결과만 처리한다.
    if reconstructed.get("ready") is not True:
        raise ValueError("상태 재구성 검증이 완료되지 않았습니다.")

    previous = index_silver(previous_silver_rows)

    resolved = reconstructed.get("resolved") or {}
    confirmed_ids = set(resolved.get("confirmed_missing_ids") or [])

    history = reconstructed.get("history_rows") or []

    # 추정 종료 이력과 확인된 종료 공고번호가 정확히 일치해야 한다.
    # 이력이 누락되거나 중복되면 공고 갱신을 진행하지 않는다.
    inferred = {}

    for row in history:
        if row.get("is_inferred") is not True:
            continue

        key = str(row.get("desertion_no") or "").strip()

        if not key or key in inferred:
            raise ValueError(f"추정 종료 이력 키 오류: {key}")

        if (
            row.get("event_type") != "CHANGED"
            or row.get("process_state_raw") != INFERRED_CLOSED_STATE
            or row.get("status_group") != "CLOSED"
            or row.get("closure_reason_code") != "\uc0ac\uc720 \ubbf8\uc0c1"
            or row.get("confirmed_by") not in ("complete_range", "lookup")
        ):
            raise ValueError(f"추정 종료 이력 내용 불일치: {key}")

        inferred[key] = row

    if confirmed_ids != set(inferred):
        raise ValueError("확인된 종료 공고와 추정 종료 이력이 일치하지 않습니다.")

    delta = []
    unchanged_ids = []

    # 먼저 전체 대상의 유효성을 확인한 뒤에만 변경 행을 반환한다.
    for key in sorted(confirmed_ids):
        old = previous.get(key)

        if old is None:
            raise ValueError(f"기존 Silver에 없는 종료 공고: {key}")

        old_state = old.get("process_state_raw")
        old_group = old.get("status_group")

        # 동일한 종료 상태가 이미 반영된 경우 재실행해도 변경하지 않는다.
        if (
            old_state == INFERRED_CLOSED_STATE
            and old_group == "CLOSED"
            and old.get("closure_reason_code") == "\uc0ac\uc720 \ubbf8\uc0c1"
        ):
            unchanged_ids.append(key)
            continue

        # 현재 보호중 상태만 추정 종료 대상으로 허용한다.
        # 실제 종료 또는 알 수 없는 상태를 강제로 덮어쓰지 않는다.
        normalized_group, _ = parse_status(old_state)

        if old_group != "PROTECTING" or normalized_group != "PROTECTING":
            raise ValueError(f"보호중이 아닌 공고의 추정 종료 차단: {key}")

        # 기존 컬럼을 모두 보존하고 상태 3개만 변경한다.
        new = dict(old)
        new["process_state_raw"] = INFERRED_CLOSED_STATE
        new["status_group"] = "CLOSED"
        new["closure_reason_code"] = "\uc0ac\uc720 \ubbf8\uc0c1"

        delta.append(new)

    return {
        "ready": True,
        "delta_rows": delta,
        "closure_ids": sorted(confirmed_ids),
        "unchanged_ids": unchanged_ids,
        "update_count": len(delta),
        "unchanged_count": len(unchanged_ids),
    }
