"""공고 종료 근거 확인 및 잘못된 종료 차단 테스트."""

from copy import deepcopy

from rebom.storage.silver.transform_notice import transform_notice
from rebom.storage.silver.validate_closure_evidence import (
    validate_closure_evidence,
)


previous = [
    transform_notice({
        "desertionNo": "A",
        "processState": "보호중",
        "happenDt": "20261015",
    }),
    transform_notice({
        "desertionNo": "B",
        "processState": "보호중",
        "happenDt": "20261020",
    }),
]

partitions = [
    {
        "kind": kind,
        "result_code": "00",
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
        "total_count": 10,
        "received_count": 10,
        "complete": True,
        "reason": "complete",
        "reprocess_ids": [],
    }
    for kind in ("list_notice", "list_protect")
]

events = [
    {
        "event": "vanished",
        "desertionNo": "A",
        "happenDt": "20261015",
        "confirmed_by": "complete_range",
    },
    {
        "event": "vanished",
        "desertionNo": "B",
        "happenDt": "20261020",
        "confirmed_by": "lookup",
    },
]

verified = {
    "partitions": partitions,
    "reprocess_ids": [],
    "rows_by_kind": {
        "events": events,
        "lookup": [{
            "desertionNo": "B",
            "happenDt": "20261020",
            "processState": "종료(입양)",
        }],
    },
}

# 1. 두 종료 근거가 유효한지 확인한다.
result = validate_closure_evidence(verified, previous)

assert result["ready"]
assert len(result["validated"]) == 2

print("COMPLETE_RANGE_VALIDATED: PASS")
print("LOOKUP_VALIDATED: PASS")


# 2. protect 구간이 불완전하면 구간 근거만으로 종료할 수 없다.
case = deepcopy(verified)
case["partitions"][1]["received_count"] = 9
case["partitions"][1]["complete"] = False
case["partitions"][1]["reason"] = "count_mismatch"

result = validate_closure_evidence(case, previous)

assert not result["ready"]
assert any(
    item["desertion_no"] == "A"
    and item["reason"] == "list_protect_incomplete"
    for item in result["rejected"]
)

print("INCOMPLETE_RANGE_BLOCKED: PASS")


# 3. 공고 발생일이 조회 범위 밖이면 종료할 수 없다.
case = deepcopy(verified)
case["partitions"][0]["start_date"] = "2026-10-16"

result = validate_closure_evidence(case, previous)

assert not result["ready"]
assert any(
    item["desertion_no"] == "A"
    and item["reason"] == "list_notice_coverage_invalid"
    for item in result["rejected"]
)

print("OUT_OF_RANGE_BLOCKED: PASS")


# 4. 개별 조회가 없으면 lookup 근거 종료를 차단한다.
case = deepcopy(verified)
case["rows_by_kind"]["lookup"] = []

result = validate_closure_evidence(case, previous)

assert not result["ready"]
assert any(
    item["desertion_no"] == "B"
    and item["reason"] == "lookup_closed_record_missing"
    for item in result["rejected"]
)

print("LOOKUP_EVIDENCE_MISSING_BLOCKED: PASS")


# 5. 재처리 대상으로 남은 공고는 종료로 확정하지 않는다.
case = deepcopy(verified)
case["reprocess_ids"] = ["A"]

result = validate_closure_evidence(case, previous)

assert not result["ready"]
assert any(
    item["desertion_no"] == "A"
    and item["reason"] == "reprocess_pending"
    for item in result["rejected"]
)

print("REPROCESS_ID_BLOCKED: PASS")

print("ICEBERG_WRITE: NOT_PERFORMED")
print("ACTUAL_A_TEAM_BRONZE: NOT_TESTED")
print("RESULT: PASS")
