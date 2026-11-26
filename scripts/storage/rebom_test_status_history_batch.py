"""종료·복귀 상태 이력과 수집 근거를 로컬 Iceberg에 저장·검증한다."""

import json

import pyarrow as pa

from rebom.storage.silver.apply_notice_pipeline import get_local_catalog
from rebom.storage.silver.prepare_status_history_batch import (
    prepare_status_history_batch,
)


# 1. 이전·현재 상태 전체를 구성한다.
#
# A: 보호중이었지만 현재 목록에서 사라짐
# B: 보호중에서 실제 종료(입양) 상태로 변경
# D: 종료(입양)에서 다시 보호중으로 복귀
#
# C는 별도로 충돌 차단만 확인한다.
previous = [
    {"desertionNo": "A", "processState": "보호중"},
    {"desertionNo": "B", "processState": "보호중"},
    {"desertionNo": "D", "processState": "종료(입양)"},
]

current = [
    {"desertionNo": "B", "processState": "종료(입양)"},
    {"desertionNo": "D", "processState": "보호중"},
]

events = [
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
        "desertionNo": "D",
    },
]

run_id = "20261102T160000-abcdef"
fetched_at = "2026-11-02T16:00:00+09:00"


# 2. 기존 B팀 이벤트 분류·상태 변환 코드를 통합 호출한다.
prepared = prepare_status_history_batch(
    previous_records=previous,
    current_records=current,
    events=events,
    run_id=run_id,
    fetched_at=fetched_at,
    previous_observed_on="2026-11-01",
)

assert prepared["ready"]
assert len(prepared["history_rows"]) == 6
assert prepared["statistics"]["inferred_closed"] == 1

rows = prepared["history_rows"]

inferred = [
    r for r in rows
    if r["desertion_no"] == "A" and r["is_inferred"]
]

assert len(inferred) == 1
assert inferred[0]["confirmed_by"] == "complete_range"
assert inferred[0]["process_state_raw"] == "종료(사유 미상)"

actual_closed = [
    r for r in rows
    if r["desertion_no"] == "B" and r["event_type"] == "CHANGED"
]

assert len(actual_closed) == 1
assert actual_closed[0]["process_state_raw"] == "종료(입양)"
assert actual_closed[0]["confirmed_by"] == "lookup"
assert not actual_closed[0]["is_inferred"]

returned = [
    r for r in rows
    if r["desertion_no"] == "D" and r["event_type"] == "CHANGED"
]

assert len(returned) == 1
assert returned[0]["process_state_raw"] == "보호중"

print("INFERRED_CLOSED: PASS")
print("ACTUAL_CLOSED: PASS")
print("RETURNED: PASS")
print("PREPARED_HISTORY_ROWS:", len(rows))


# 3. 충돌 공고가 있는 실행은 전체 이력 저장을 차단한다.
conflict_previous = previous + [
    {"desertionNo": "C", "processState": "보호중"}
]

conflict_current = current + [
    {"desertionNo": "C", "processState": "보호중"}
]

conflict_events = events + [
    {
        "event": "vanished",
        "desertionNo": "C",
        "confirmed_by": "complete_range",
    }
]

blocked = prepare_status_history_batch(
    previous_records=conflict_previous,
    current_records=conflict_current,
    events=conflict_events,
    run_id=run_id,
    fetched_at=fetched_at,
    previous_observed_on="2026-11-01",
)

assert not blocked["ready"]
assert blocked["history_rows"] == []
assert len(blocked["conflicts"]) == 1

print("CONFLICT_WRITE_BLOCKED: PASS")


# 4. 기존 상태 이력 테이블의 스키마를 기준으로
# 실행 메타데이터 컬럼 4개를 추가한 테스트 테이블을 만든다.
catalog = get_local_catalog()

source_name = "rebom_silver.silver_animal_status_history"
test_name = "rebom_silver.silver_animal_status_history_evidence_test"

source = catalog.load_table(source_name)
base_schema = source.schema().as_arrow()

extra_fields = [
    pa.field("run_id", pa.large_string(), nullable=False),
    pa.field("fetched_at", pa.large_string()),
    pa.field("confirmed_by", pa.large_string()),
    pa.field("is_inferred", pa.bool_()),
]

test_schema = pa.schema(list(base_schema) + extra_fields)

if not catalog.table_exists(test_name):
    catalog.create_table(test_name, schema=test_schema)
    print("CREATED_TEST_TABLE:", test_name)

table = catalog.load_table(test_name)

if table.schema().as_arrow() != test_schema:
    raise RuntimeError("테스트 이력 테이블 스키마 불일치")


# 5. 동일 이력을 구별하는 테스트용 키를 정의한다.
# 이 테스트에서 run_id가 같아야 동일 실행으로 간주한다.
#
# 운영 이력의 영구 식별자 및 실행 간 중복 방지 규칙은
# 향후 별도 설계가 필요하다.
key_columns = [
    "run_id",
    "desertion_no",
    "observed_on",
    "event_type",
]


def index_history(records):
    result = {}

    for row in records:
        key = tuple(row[name] for name in key_columns)

        if key in result:
            raise ValueError(f"중복 상태 이력 키: {key}")

        result[key] = row

    return result


# 6. 기존 테스트 데이터가 있다면 예상 내용인지 확인한다.
existing = table.scan().to_arrow().to_pylist()

if existing and index_history(existing) != index_history(rows):
    raise RuntimeError(
        "테스트 테이블에 예상하지 못한 이력이 있습니다."
    )

arrow = pa.Table.from_pylist(
    rows,
    schema=table.schema().as_arrow(),
)


# 7. 테스트 Iceberg 테이블에 이력 6건을 UPSERT한다.
result = table.upsert(
    arrow,
    join_cols=key_columns,
)

print("HISTORY_INSERTED:", result.rows_inserted)
print("HISTORY_UPDATED:", result.rows_updated)

stored = catalog.load_table(test_name).scan().to_arrow().to_pylist()

assert index_history(stored) == index_history(rows)
assert len(stored) == 6

print("VERIFIED_HISTORY_ROWS:", len(stored))


# 8. 동일 실행을 반복한다.
# 이력은 누적돼야 하지만 같은 실행으로 중복되면 안 된다.
rerun = catalog.load_table(test_name).upsert(
    arrow,
    join_cols=key_columns,
)

assert rerun.rows_inserted == 0
assert rerun.rows_updated == 0

print("RERUN_INSERTED:", rerun.rows_inserted)
print("RERUN_UPDATED:", rerun.rows_updated)

final = catalog.load_table(test_name).scan().to_arrow().to_pylist()

assert index_history(final) == index_history(rows)

print("FINAL_HISTORY_ROWS:", len(final))
print("PRODUCTION_HISTORY: NOT_MODIFIED")
print("ACTUAL_A_TEAM_EVENTS: NOT_TESTED")
print("RESULT: PASS")
