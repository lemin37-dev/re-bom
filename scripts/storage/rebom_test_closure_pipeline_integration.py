"""검증된 종료 근거만 상태 재구성에 반영되는지 확인한다."""

from copy import deepcopy

from rebom.storage.silver.reconstruct_status_snapshot import (
    reconstruct_status_snapshot,
)
from rebom.storage.silver.transform_notice import transform_notice


def notice(key, state):
    return {
        "desertionNo": key,
        "processState": state,
        "happenDt": "20261015",
    }


previous_raw = [
    notice("A", "보호중"),
    notice("B", "보호중"),
    notice("C", "종료(입양)"),
    notice("D", "보호중"),
]

previous_silver = [transform_notice(row) for row in previous_raw]

delta_raw = [
    notice("B", "종료(입양)"),
    notice("C", "보호중"),
    notice("E", "보호중"),
]

prepared = {
    "ready": True,
    "delta_rows": [transform_notice(row) for row in delta_raw],
}

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

verified = {
    "run_id": "20261103T090000-abcdef",
    "fetched_at": "2026-11-03T09:00:00+09:00",
    "partitions": partitions,
    "reprocess_ids": [],
    "rows_by_kind": {
        "updated": delta_raw,
        "list_notice": [],
        "list_protect": [],
        "lookup": [notice("B", "종료(입양)")],
        "events": [
            {
                "event": "vanished",
                "desertionNo": "A",
                "happenDt": "20261015",
                "confirmed_by": "complete_range",
            },
            {
                "event": "vanished",
                "desertionNo": "B",
                "happenDt": "20261015",
                "confirmed_by": "lookup",
            },
            {
                "event": "returned",
                "desertionNo": "C",
                "happenDt": "20261015",
            },
        ],
    },
}


def execute(run):
    return reconstruct_status_snapshot(
        verified_run=run,
        previous_silver_rows=previous_silver,
        prepared_notice=prepared,
        previous_observed_on="2026-11-02",
    )


# 정상 종료 근거를 보유한 실행은 처리한다.
result = execute(verified)

assert result["ready"]
assert result["resolved"]["confirmed_missing_ids"] == ["A"]
assert result["resolved"]["actual_closed_ids"] == ["B"]
assert result["resolved"]["returned_ids"] == ["C"]
assert len(result["history_rows"]) == 4

print("VALID_EVIDENCE_PIPELINE: PASS")
print("HISTORY_ROWS:", len(result["history_rows"]))


# 보호중 목록의 수집이 불완전한 경우 종료를 차단한다.
invalid_range = deepcopy(verified)
invalid_range["partitions"][1]["received_count"] = 9
invalid_range["partitions"][1]["complete"] = False
invalid_range["partitions"][1]["reason"] = "count_mismatch"

result = execute(invalid_range)

assert not result["ready"]
assert result["history_rows"] == []
assert any(
    item["desertion_no"] == "A"
    and item["reason"] == "list_protect_incomplete"
    for item in result["conflicts"]
)

print("INCOMPLETE_RANGE_PIPELINE_BLOCKED: PASS")


# 실제 lookup 종료 레코드가 없다면 종료 처리를 차단한다.
invalid_lookup = deepcopy(verified)
invalid_lookup["rows_by_kind"]["lookup"] = []

result = execute(invalid_lookup)

assert not result["ready"]
assert result["history_rows"] == []
assert any(
    item["desertion_no"] == "B"
    and item["reason"] == "lookup_closed_record_missing"
    for item in result["conflicts"]
)

print("MISSING_LOOKUP_PIPELINE_BLOCKED: PASS")


# 발생일이 기존 Silver와 맞지 않아도 종료할 수 없다.
invalid_date = deepcopy(verified)
invalid_date["rows_by_kind"]["events"][0]["happenDt"] = "20261016"

result = execute(invalid_date)

assert not result["ready"]
assert result["history_rows"] == []

print("HAPPEN_DATE_MISMATCH_BLOCKED: PASS")


# 재처리 대상은 이번 실행에서 종료시켜서는 안 된다.
pending = deepcopy(verified)
pending["reprocess_ids"] = ["A"]

result = execute(pending)

assert not result["ready"]
assert result["history_rows"] == []

print("REPROCESS_PIPELINE_BLOCKED: PASS")

print("ICEBERG_WRITE: NOT_PERFORMED")
print("ACTUAL_A_TEAM_BRONZE: NOT_TESTED")
print("RESULT: PASS")
