"""Bronze 중복·충돌 및 Silver 증분 준비 검증."""

from copy import deepcopy

from rebom.storage.silver.prepare_notice_delta import prepare_notice_delta
from rebom.storage.silver.transform_notice import transform_notice


# 최초 Silver에 A, B 공고가 저장돼 있다고 가정한다.
previous = [
    {
        "desertionNo": "A",
        "processState": "보호중",
        "specialMark": "사람을 좋아함",
        "updTm": "2026-11-01 10:00:00.0",
    },
    {
        "desertionNo": "B",
        "processState": "보호중",
        "specialMark": "활발함",
    },
]

existing = [transform_notice(row) for row in previous]


def make_run(updated, notice=None, protect=None):
    return {
        "run_id": "20261102T160000-abcdef",
        "rows_by_kind": {
            "updated": updated,
            "list_notice": notice or [],
            "list_protect": protect or [],
            "events": [],
        },
    }


# 1. A는 updTm만 변경됐으므로 실제 변경으로 보지 않는다.
# B는 Bronze에 없지만 기존 Silver에 계속 남아 있어야 한다.
# C는 새로 들어왔으므로 INSERT 대상이다.
changed_time = deepcopy(previous[0])
changed_time["updTm"] = "2026-11-02 10:00:00.0"

new_notice = {
    "desertionNo": "C",
    "processState": "보호중",
    "specialMark": "온순함",
}

run = make_run(
    updated=[changed_time, new_notice],
    notice=[deepcopy(changed_time)],
)

result = prepare_notice_delta(run, existing)

assert result["ready"] is True
assert result["total_input"] == 3
assert result["unique_count"] == 2
assert result["duplicate_count"] == 1
assert result["insert_count"] == 1
assert result["update_count"] == 0
assert result["unchanged_count"] == 1
assert [r["desertion_no"] for r in result["delta_rows"]] == ["C"]

print("IDENTICAL_DUPLICATE_REMOVED: PASS")
print("UPDTM_ONLY_IGNORED: PASS")
print("INSERT_PREPARED:", result["insert_count"])
print("UNCHANGED:", result["unchanged_count"])


# 2. A의 특징 문장이 실제 변경되면 UPDATE 대상으로 만든다.
content_changed = deepcopy(previous[0])
content_changed["specialMark"] = "다른 동물과 잘 지냄"

result = prepare_notice_delta(
    make_run(updated=[content_changed]),
    existing,
)

assert result["ready"] is True
assert result["update_count"] == 1
assert result["insert_count"] == 0
assert len(result["delta_rows"]) == 1

print("REAL_CONTENT_CHANGE_DETECTED: PASS")


# 3. 동일 실행의 두 출처에서 A의 상태가 다르면 충돌이다.
# updated가 더 우선이라는 임시 규칙을 적용하면 안 된다.
closed = deepcopy(previous[0])
closed["processState"] = "종료(입양)"

result = prepare_notice_delta(
    make_run(
        updated=[closed],
        notice=[deepcopy(previous[0])],
    ),
    existing,
)

assert result["ready"] is False
assert result["delta_rows"] == []
assert result["conflicts"][0]["reason"] == "raw_content_conflict"

print("STATUS_CONFLICT_BLOCKED: PASS")


# 4. 같은 공고의 사진 URL만 달라도 일단 충돌을 기록한다.
# 사진 처리와 병합 우선순위는 실제 샘플 검증 후 결정한다.
photo_a = deepcopy(previous[0])
photo_b = deepcopy(previous[0])

photo_a["popfile1"] = "https://example.org/a.jpg"
photo_b["popfile1"] = "https://example.org/b.jpg"

result = prepare_notice_delta(
    make_run(updated=[photo_a], notice=[photo_b]),
    existing,
)

assert result["ready"] is False
assert result["delta_rows"] == []

print("PHOTO_CONFLICT_BLOCKED: PASS")


# 5. 기존 목록에서 누락된 공고를 삭제하거나 종료하지 않는다.
# 이번 모듈은 공고 증분 생성 전용이다.
print("PRODUCTION_ICEBERG_WRITE: NOT_PERFORMED")
print("ACTUAL_A_TEAM_BRONZE: NOT_TESTED")
print("RESULT: PASS")
