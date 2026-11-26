"""A팀 종료·복귀 이벤트를 수집 근거가 포함된 Silver 상태 이력으로 변환한다."""

from datetime import date

from rebom.storage.silver.resolve_ingest_events import resolve_ingest_events
from rebom.storage.silver.transform_status_history import transform_status_history


def prepare_status_history_batch(
    previous_records,
    current_records,
    events,
    run_id,
    fetched_at,
    previous_observed_on,
):
    # 이전/현재 입력은 비교 가능한 전체 상태 스냅샷이어야 한다.
    # updated.jsonl.gz처럼 일부 공고만 있는 증분 파일을
    # current_records로 직접 전달하면 안 된다.
    #
    # 이 함수는 Iceberg에 쓰지 않는다.
    # 문제가 없을 때만 적재 가능한 이력 행을 돌려준다.

    if not run_id or not fetched_at:
        raise ValueError("수집 실행 메타데이터가 없습니다.")

    # 1. A팀 이벤트를 현재 상태와 대조한다.
    # vanished가 있어도 현재 보호중이면 종료로 처리하지 않는다.
    resolved = resolve_ingest_events(
        previous_records,
        current_records,
        events,
    )

    # 2. 충돌이 있다면 일부만 저장하지 않고 실행 전체를 중단한다.
    # 충돌 검토 및 부분 재처리 정책은 실제 샘플 확인 후 결정한다.
    if resolved["conflicts"]:
        return {
            "ready": False,
            "history_rows": [],
            "conflicts": resolved["conflicts"],
            "statistics": {},
        }

    # 3. 기존 B팀 상태 이력 변환 함수를 재사용한다.
    # 실제 종료 공고는 일반 상태 변경으로,
    # 확정된 사라짐만 '종료(사유 미상)'으로 기록한다.
    current_observed_on = date.fromisoformat(
        fetched_at[:10]
    ).isoformat()

    history, statistics = transform_status_history(
        previous_records=previous_records,
        current_records=current_records,
        previous_observed_on=previous_observed_on,
        current_observed_on=current_observed_on,
        confirmed_missing_ids=resolved["confirmed_missing_ids"],
    )

    inferred_ids = set(resolved["confirmed_missing_ids"])
    evidence = resolved["confirmation_evidence"]

    # 4. 각 이력에 실행 메타데이터를 붙인다.
    # confirmed_by는 A팀에서 종료 근거가 확인된 공고에만 붙인다.
    enriched = []

    for row in history:
        item = dict(row)
        key = item["desertion_no"]

        is_inferred = (
            key in inferred_ids
            and item["event_type"] == "CHANGED"
            and item["observed_on"] == current_observed_on
            and item["process_state_raw"] == "종료(사유 미상)"
        )

        item["run_id"] = run_id
        item["fetched_at"] = fetched_at
        item["confirmed_by"] = (
            evidence.get(key)
            if item["event_type"] == "CHANGED"
            and item["observed_on"] == current_observed_on
            else None
        )
        item["is_inferred"] = is_inferred

        enriched.append(item)

    return {
        "ready": True,
        "history_rows": enriched,
        "conflicts": [],
        "statistics": statistics,
    }
