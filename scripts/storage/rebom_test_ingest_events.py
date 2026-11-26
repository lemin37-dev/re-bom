"""종료·복귀 이벤트 충돌 처리와 기존 상태 이력 함수 연결 테스트."""

from rebom.storage.silver.resolve_ingest_events import resolve_ingest_events
from rebom.storage.silver.transform_status_history import (
    transform_status_history,
)


# 1. 테스트용으로 네 가지 서로 다른 상황을 준비한다.
# A: 공고가 사라지고 종료가 확인됨
# B: 사라졌다는 이벤트가 있지만 실제 종료 상태도 조회됨
# C: 사라짐 이벤트와 달리 현재도 보호중임
# D: 기존 종료 상태에서 보호중으로 복귀함
previous = [
    {"desertionNo": "A", "processState": "보호중"},
    {"desertionNo": "B", "processState": "보호중"},
    {"desertionNo": "C", "processState": "보호중"},
    {"desertionNo": "D", "processState": "종료(입양)"},
]

current = [
    {"desertionNo": "B", "processState": "종료(입양)"},
    {"desertionNo": "C", "processState": "보호중"},
    {"desertionNo": "D", "processState": "보호중"},
]

events = [
    {"event": "vanished", "desertionNo": "A", "confirmed_by": "complete_range"},
    {"event": "vanished", "desertionNo": "B", "confirmed_by": "lookup"},
    {"event": "vanished", "desertionNo": "C", "confirmed_by": "complete_range"},
    {"event": "returned", "desertionNo": "D"},
]


# 2. A팀 이벤트를 안전하게 분류한다.
result = resolve_ingest_events(previous, current, events)

print("CONFIRMED_MISSING:", result["confirmed_missing_ids"])
print("ACTUAL_CLOSED:", result["actual_closed_ids"])
print("RETURNED:", result["returned_ids"])
print("CONFLICTS:", result["conflicts"])
print("EVIDENCE:", result["confirmation_evidence"])

assert result["confirmed_missing_ids"] == ["A"]
assert result["actual_closed_ids"] == ["B"]
assert result["returned_ids"] == ["D"]
assert result["conflicts"] == [
    ("C", "vanished", "current_state_conflict")
]


# 3. 기존 B팀 상태 이력 함수에 확정된 누락 공고만 전달한다.
# B는 현재 스냅샷에 있으므로 confirmed_missing_ids에 포함하지 않는다.
history, stats = transform_status_history(
    previous_records=previous,
    current_records=current,
    previous_observed_on="2026-11-01",
    current_observed_on="2026-11-02",
    confirmed_missing_ids=result["confirmed_missing_ids"],
)

print("HISTORY_ROWS:", len(history))
print("INFERRED_CLOSED:", stats["inferred_closed"])

assert len(history) == 7
assert stats["inferred_closed"] == 1

# 4. 추정 종료 A, 실제 종료 B, 복귀 D의 이력 내용을 확인한다.
current_events = [
    row for row in history
    if row["observed_on"] == "2026-11-02"
]

by_id = {row["desertion_no"]: row for row in current_events}

assert by_id["A"]["process_state_raw"] == "종료(사유 미상)"
assert by_id["B"]["process_state_raw"] == "종료(입양)"
assert by_id["D"]["process_state_raw"] == "보호중"
assert "C" not in by_id

# 5. 같은 이벤트가 여러 번 들어와도 분류 결과는 같아야 한다.
rerun = resolve_ingest_events(previous, current, events + events)

assert rerun == result

# 6. 실제 Iceberg 적재는 수행하지 않는다.
# confirmed_by 보존은 현재 반환 메타데이터에서만 검증한다.
print("EVENT_CLASSIFICATION: PASS")
print("HISTORY_INTEGRATION: PASS")
print("ICEBERG_WRITE: NOT_PERFORMED")
print("RESULT: PASS")
