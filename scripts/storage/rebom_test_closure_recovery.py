"""Silver 공고 저장 후 이력 저장이 중단된 상황의 복구 검증."""

import argparse
from datetime import date

import pyarrow as pa

from rebom.storage.silver.apply_notice_pipeline import get_local_catalog
from rebom.storage.silver.prepare_notice_closure import (
    INFERRED_CLOSED_STATE,
    prepare_notice_closure,
)
from rebom.storage.silver.transform_notice import transform_notice


NOTICE_TEST = "rebom_silver.silver_animal_notice_closure_recovery_test"
HISTORY_TEST = "rebom_silver.silver_animal_status_history_closure_recovery_test"

NOTICE_SOURCE = "rebom_silver.silver_animal_notice"
HISTORY_SOURCE = "rebom_silver.silver_animal_status_history"

RUN_ID = "closure-recovery-fixture-v1"

parser = argparse.ArgumentParser()
parser.add_argument("--phase", choices=["interrupt", "recover"], required=True)
args = parser.parse_args()

catalog = get_local_catalog()

original = transform_notice({
    "desertionNo": "RECOVERY-A",
    "processState": "\ubcf4\ud638\uc911",
    "happenDt": "20261015",
    "careRegNo": "TEST-SHELTER",
})

history_row = {
    "desertion_no": "RECOVERY-A",
    "observed_on": "2026-11-03",
    "event_type": "CHANGED",
    "previous_state_raw": "\ubcf4\ud638\uc911",
    "process_state_raw": INFERRED_CLOSED_STATE,
    "status_group": "CLOSED",
    "closure_reason_code": "\uc0ac\uc720 \ubbf8\uc0c1",
    "source_updated_at_raw": None,
    "run_id": RUN_ID,
    "fetched_at": "2026-11-03T10:00:00+09:00",
    "confirmed_by": "complete_range",
    "is_inferred": True,
}

# 이번 테스트는 종료 근거 검증이 끝난 결과를 모의 입력으로 사용한다.
# 실제 A팀 manifest를 검증하는 테스트가 아니다.
reconstructed = {
    "ready": True,
    "resolved": {"confirmed_missing_ids": ["RECOVERY-A"]},
    "history_rows": [history_row],
}


def read_rows(name):
    return catalog.load_table(name).scan().to_arrow().to_pylist()


def insert_or_update(name, rows, keys):
    table = catalog.load_table(name)
    arrow = pa.Table.from_pylist(rows, schema=table.schema().as_arrow())

    return table.upsert(
        arrow,
        join_cols=keys,
        when_matched_update_all=True,
        when_not_matched_insert_all=True,
    )


def history_key(row):
    return (
        row["run_id"],
        row["desertion_no"],
        row["observed_on"],
        row["event_type"],
    )


if args.phase == "interrupt":
    # 테스트 테이블이 이미 있다면 기존 데이터를 덮거나 삭제하지 않는다.
    if catalog.table_exists(NOTICE_TEST) or catalog.table_exists(HISTORY_TEST):
        raise RuntimeError("테스트 테이블이 이미 존재합니다. 초기화 중단")

    notice_schema = catalog.load_table(NOTICE_SOURCE).schema().as_arrow()
    history_base = catalog.load_table(HISTORY_SOURCE).schema().as_arrow()

    history_schema = pa.schema(list(history_base) + [
        pa.field("run_id", pa.large_string(), nullable=False),
        pa.field("fetched_at", pa.large_string()),
        pa.field("confirmed_by", pa.large_string()),
        pa.field("is_inferred", pa.bool_()),
    ])

    catalog.create_table(NOTICE_TEST, schema=notice_schema)
    catalog.create_table(HISTORY_TEST, schema=history_schema)

    # 먼저 보호중 공고의 최초 상태를 저장한다.
    inserted = insert_or_update(
        NOTICE_TEST, [original], ["desertion_no"]
    )

    assert inserted.rows_inserted == 1

    # 검증된 추정 종료 변경분을 준비한다.
    prepared = prepare_notice_closure([original], reconstructed)

    assert prepared["update_count"] == 1

    # 공고 테이블에만 추정 종료를 저장한다.
    updated = insert_or_update(
        NOTICE_TEST,
        prepared["delta_rows"],
        ["desertion_no"],
    )

    assert updated.rows_updated == 1

    stored = read_rows(NOTICE_TEST)

    assert len(stored) == 1
    assert stored[0]["status_group"] == "CLOSED"
    assert stored[0]["process_state_raw"] == INFERRED_CLOSED_STATE
    assert stored[0]["happen_date"] == date(2026, 10, 15)

    # 실제 이력 테이블 쓰기 전에 실행이 중단된 상황을 만든다.
    assert read_rows(HISTORY_TEST) == []

    print("NOTICE_CLOSURE_SAVED: PASS")
    print("HISTORY_ROWS_BEFORE_FAILURE: 0")
    print("SIMULATED_INTERRUPTION: PASS")
    print("PRODUCTION_TABLES: NOT_MODIFIED")


elif args.phase == "recover":
    if not catalog.table_exists(NOTICE_TEST):
        raise RuntimeError("공고 테스트 테이블이 없습니다.")

    if not catalog.table_exists(HISTORY_TEST):
        raise RuntimeError("이력 테스트 테이블이 없습니다.")

    stored_notice = read_rows(NOTICE_TEST)
    stored_history = read_rows(HISTORY_TEST)

    assert len(stored_notice) == 1
    assert stored_notice[0]["desertion_no"] == "RECOVERY-A"
    assert stored_notice[0]["status_group"] == "CLOSED"
    assert stored_notice[0]["process_state_raw"] == INFERRED_CLOSED_STATE

    # 이전 단계에서 중단돼 이력이 없거나,
    # 이미 복구를 완료해 이력 1건이 있는 상황만 허용한다.
    assert len(stored_history) in (0, 1)

    if stored_history:
        assert stored_history[0] == history_row

    # 동일 종료를 다시 준비해도 공고 변경분은 0건이어야 한다.
    prepared = prepare_notice_closure(stored_notice, reconstructed)

    assert prepared["update_count"] == 0
    assert prepared["unchanged_count"] == 1

    print("CLOSURE_STATE_PRESERVED: PASS")
    print("RERUN_NOTICE_UPDATES:", prepared["update_count"])

    # 누락된 이력을 동일 실행 키로 UPSERT한다.
    keys = ["run_id", "desertion_no", "observed_on", "event_type"]

    result = insert_or_update(HISTORY_TEST, [history_row], keys)

    expected_insert = 0 if stored_history else 1

    assert result.rows_inserted == expected_insert
    assert result.rows_updated == 0

    print("RECOVERED_HISTORY_INSERTS:", result.rows_inserted)

    final_notice = read_rows(NOTICE_TEST)
    final_history = read_rows(HISTORY_TEST)

    assert len(final_notice) == 1
    assert len(final_history) == 1
    assert final_history[0] == history_row

    # 한 번 더 실행해도 같은 상태 이력을 중복 저장하지 않는다.
    rerun = insert_or_update(HISTORY_TEST, [history_row], keys)

    assert rerun.rows_inserted == 0
    assert rerun.rows_updated == 0

    assert len(read_rows(HISTORY_TEST)) == 1

    print("HISTORY_RECOVERED: PASS")
    print("RERUN_HISTORY_INSERTS:", rerun.rows_inserted)
    print("FINAL_NOTICE_ROWS:", len(final_notice))
    print("FINAL_HISTORY_ROWS:", len(final_history))
    print("PRODUCTION_TABLES: NOT_MODIFIED")
    print("ACTUAL_A_TEAM_BRONZE: NOT_TESTED")
    print("RESULT: PASS")
