"""기존 Silver + 부분 증분 + 종료/복귀 이벤트를 조합하는 테스트."""

from rebom.storage.silver.reconstruct_status_snapshot import (
    reconstruct_status_snapshot,
)
from rebom.storage.silver.transform_notice import transform_notice


def make_notice(key, state):
    return {"desertionNo": key, "processState": state}


# 기존 Silver의 전체 공고 4건
previous = [
    make_notice("A", "보호중"),
    make_notice("B", "보호중"),
    make_notice("C", "종료(입양)"),
    make_notice("D", "보호중"),
]

previous_silver = [transform_notice(row) for row in previous]

# 이번 증분에 들어온 공고:
# B는 실제 종료, C는 보호중 복귀, E는 신규 공고
# D는 이번에 수집되지 않았지만 종료 이벤트도 없으므로 유지
delta_raw = [
    make_notice("B", "종료(입양)"),
    make_notice("C", "보호중"),
    make_notice("E", "보호중"),
]

delta_silver = [transform_notice(row) for row in delta_raw]

verified = {
    "run_id": "20261103T090000-abcdef",
    "fetched_at": "2026-11-03T09:00:00+09:00",
    "rows_by_kind": {
        "updated": delta_raw,
        "list_notice": [],
        "list_protect": [],
        "events": [
            {
                "event": "vanished",
                "desertionNo": "A",
                "confirmed_by": "complete_range",
            },
            {
                "event": "vanished",
                "desertionNo": "B",
                "confirmed_by": "lookup",
            },
            {
                "event": "returned",
                "desertionNo": "C",
            },
        ],
    },
    "reprocess_ids": [],
}

prepared = {
    "ready": True,
    "delta_rows": delta_silver,
}

result = reconstruct_status_snapshot(
    verified,
    previous_silver,
    prepared,
    previous_observed_on="2026-11-02",
)

assert result["ready"]
assert result["previous_count"] == 4
assert result["current_count"] == 4
assert result["observed_count"] == 3

assert result["resolved"]["confirmed_missing_ids"] == ["A"]
assert result["resolved"]["actual_closed_ids"] == ["B"]
assert result["resolved"]["returned_ids"] == ["C"]

history = result["history_rows"]

# A 추정 종료, B 실제 종료, C 복귀, E 최초 관측만 기록한다.
# D는 이번에 관측되지 않아도 이력을 추가하지 않는다.
assert len(history) == 4
assert {row["desertion_no"] for row in history} == {"A", "B", "C", "E"}

assert sum(row["is_inferred"] for row in history) == 1

print("PARTIAL_DELTA_MERGED: PASS")
print("CONFIRMED_MISSING:", result["resolved"]["confirmed_missing_ids"])
print("ACTUAL_CLOSED:", result["resolved"]["actual_closed_ids"])
print("RETURNED:", result["resolved"]["returned_ids"])
print("NEW_INITIAL_AND_CHANGES:", len(history))
print("UNOBSERVED_WITHOUT_EVENT_PRESERVED: PASS")


# A가 같은 실행에서 실제로 관측됐을 때는 추정 종료하면 안 된다.
# 변경분은 없더라도 원본 관측 목록에는 들어 있을 수 있다.
observed_conflict = dict(verified)
observed_conflict["rows_by_kind"] = dict(verified["rows_by_kind"])
observed_conflict["rows_by_kind"]["list_protect"] = [
    make_notice("A", "보호중")
]

blocked = reconstruct_status_snapshot(
    observed_conflict,
    previous_silver,
    prepared,
    previous_observed_on="2026-11-02",
)

assert not blocked["ready"]
assert blocked["history_rows"] == []
assert blocked["conflicts"][0][0] == "A"

print("VANISHED_BUT_OBSERVED_BLOCKED: PASS")

print("ICEBERG_WRITE: NOT_PERFORMED")
print("ACTUAL_A_TEAM_BRONZE: NOT_TESTED")
print("RESULT: PASS")
