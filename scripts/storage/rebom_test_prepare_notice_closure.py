"""Silver 추정 종료 상태 변경분 생성 및 재실행 검증."""

from copy import deepcopy

from rebom.storage.silver.prepare_notice_closure import (
    INFERRED_CLOSED_STATE,
    prepare_notice_closure,
)
from rebom.storage.silver.transform_notice import transform_notice


def notice(key, state):
    return transform_notice({
        "desertionNo": key,
        "processState": state,
        "happenDt": "20261015",
        "careRegNo": "SHELTER-001",
    })


previous = [
    notice("A", "\ubcf4\ud638\uc911"),
    notice("B", "\uc885\ub8cc(\uc785\uc591)"),
    notice("C", "\ubcf4\ud638\uc911"),
]

inferred_history = {
    "desertion_no": "A",
    "event_type": "CHANGED",
    "process_state_raw": INFERRED_CLOSED_STATE,
    "status_group": "CLOSED",
    "closure_reason_code": "\uc0ac\uc720 \ubbf8\uc0c1",
    "confirmed_by": "complete_range",
    "is_inferred": True,
}

reconstructed = {
    "ready": True,
    "resolved": {
        "confirmed_missing_ids": ["A"],
    },
    "history_rows": [inferred_history],
}


# 1. 검증된 추정 종료 공고만 변경분으로 생성한다.
result = prepare_notice_closure(previous, reconstructed)

assert result["ready"]
assert result["update_count"] == 1
assert result["closure_ids"] == ["A"]

updated = result["delta_rows"][0]
old = previous[0]

assert updated["process_state_raw"] == INFERRED_CLOSED_STATE
assert updated["status_group"] == "CLOSED"
assert updated["closure_reason_code"] == "\uc0ac\uc720 \ubbf8\uc0c1"

# 상태 3개를 제외한 기존 공고 정보가 그대로인지 확인한다.
state_fields = {
    "process_state_raw",
    "status_group",
    "closure_reason_code",
}

for key in old:
    if key not in state_fields:
        assert updated[key] == old[key], key

print("INFERRED_CLOSURE_PREPARED: PASS")
print("NOTICE_UPDATED:", result["update_count"])
print("EXISTING_FIELDS_PRESERVED: PASS")


# 2. 같은 종료 상태를 다시 반영해도 변경분은 생성되지 않는다.
after_first_apply = [
    updated if row["desertion_no"] == "A" else row
    for row in previous
]

rerun = prepare_notice_closure(after_first_apply, reconstructed)

assert rerun["update_count"] == 0
assert rerun["unchanged_ids"] == ["A"]

print("RERUN_UPDATE_COUNT:", rerun["update_count"])
print("IDEMPOTENT_CLOSURE: PASS")


# 3. 이미 실제 종료된 공고를 추정 종료로 덮어쓰지 않는다.
invalid = deepcopy(reconstructed)
invalid["resolved"]["confirmed_missing_ids"] = ["B"]
invalid["history_rows"][0]["desertion_no"] = "B"

try:
    prepare_notice_closure(previous, invalid)
except ValueError:
    print("ACTUAL_CLOSURE_OVERWRITE_BLOCKED: PASS")
else:
    raise AssertionError("실제 종료 공고 덮어쓰기 차단 실패")


# 4. 종료 근거 검증에 실패한 결과는 적용할 수 없다.
invalid = deepcopy(reconstructed)
invalid["ready"] = False

try:
    prepare_notice_closure(previous, invalid)
except ValueError:
    print("UNVERIFIED_CLOSURE_BLOCKED: PASS")
else:
    raise AssertionError("미검증 종료 처리 차단 실패")


# 5. 종료 이력이 누락된 경우 공고만 갱신하지 않는다.
invalid = deepcopy(reconstructed)
invalid["history_rows"] = []

try:
    prepare_notice_closure(previous, invalid)
except ValueError:
    print("MISSING_HISTORY_BLOCKED: PASS")
else:
    raise AssertionError("종료 이력 누락 차단 실패")


print("ICEBERG_WRITE: NOT_PERFORMED")
print("ACTUAL_A_TEAM_BRONZE: NOT_TESTED")
print("RESULT: PASS")
