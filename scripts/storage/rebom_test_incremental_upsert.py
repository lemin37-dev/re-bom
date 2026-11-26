import json
import os
from pathlib import Path

import pyarrow as pa
from pyiceberg.catalog import load_catalog

from rebom.storage.silver.transform_notice import transform_notice


# 1. A팀이 제공한 두 시점의 샘플 데이터를 읽는다.
# 첫 번째 파일은 10월 28일, 두 번째 파일은 10월 30일 데이터다.
# 이를 이용해 실제로 새 공고가 생기거나 기존 공고가 변경된 상황을 재현한다.
sample_dir = Path("data/sample/incremental")

with (sample_dir / "t0_2026-10-28.json").open(encoding="utf-8-sig") as f:
    previous_raw = json.load(f)

with (sample_dir / "truth_2026-10-30.json").open(encoding="utf-8-sig") as f:
    current_raw = json.load(f)

if not isinstance(previous_raw, list) or not isinstance(current_raw, list):
    raise TypeError("Snapshot files must contain JSON arrays")


# 2. desertionNo를 기준으로 공고를 구분한다.
# 같은 공고번호가 두 번 나오면 어느 것이 최신인지 판단할 수 없으므로 중단한다.
def index_records(records):
    result = {}

    for row in records:
        key = str(row.get("desertionNo") or "").strip()

        if not key:
            raise ValueError("desertionNo is required")

        if key in result:
            raise ValueError(f"Duplicate desertionNo: {key}")

        result[key] = row

    return result


previous = index_records(previous_raw)
current = index_records(current_raw)

new_ids = set(current) - set(previous)
missing_ids = set(previous) - set(current)

# 사라진 공고는 A팀의 수집 완전성 검증 없이는 종료로 판단하지 않는다.
# 이번 테스트는 신규 공고와 기존 공고의 갱신만 검증한다.
if missing_ids:
    raise RuntimeError("Missing notices require A-team confirmation")

status_changed = sum(
    previous[key].get("processState") != current[key].get("processState")
    for key in set(previous) & set(current)
)

print("PREVIOUS:", len(previous))
print("CURRENT:", len(current))
print("NEW_NOTICES:", len(new_ids))
print("STATUS_CHANGED:", status_changed)

if (len(previous), len(current), len(new_ids), status_changed) != (87, 115, 28, 44):
    raise RuntimeError("Unexpected sample statistics")


# 3. 기존 B팀 Python 정제 코드를 재사용한다.
# 여기서는 새로운 정제 규칙을 만들지 않는다.
previous_rows = [transform_notice(row) for row in previous_raw]
current_rows = [transform_notice(row) for row in current_raw]


# 4. 기존 SQLite Catalog에 연결한다.
# Windows에서는 warehouse를 file URI가 아닌 일반 경로로 전달한다.
base = Path(os.environ["LOCALAPPDATA"]) / "rebom-iceberg"

catalog = load_catalog(
    "rebom_local",
    type="sql",
    uri=f"sqlite:///{(base / 'catalog.db').as_posix()}",
    warehouse=str(base / "warehouse"),
)

# 실제 Silver 테이블과 별도의 테스트 테이블을 사용한다.
source_name = "rebom_silver.silver_animal_notice"
test_name = "rebom_silver.silver_animal_notice_incremental_test"

source_table = catalog.load_table(source_name)

# 기존 Silver 테이블과 동일한 컬럼 구조로 테스트 테이블을 만든다.
# 이미 테스트 테이블이 있으면 삭제하거나 초기화하지 않는다.
if not catalog.table_exists(test_name):
    catalog.create_table(test_name, schema=source_table.schema())
    print("CREATED_TEST_TABLE:", test_name)

table = catalog.load_table(test_name)

# 5. Python dict 목록을 Iceberg에서 사용할 PyArrow 테이블로 변환한다.
# 기존 Iceberg 스키마를 사용해 컬럼 타입을 일치시킨다.
arrow_schema = table.schema().as_arrow()

previous_arrow = pa.Table.from_pylist(previous_rows, schema=arrow_schema)
current_arrow = pa.Table.from_pylist(current_rows, schema=arrow_schema)


# 6. 저장된 데이터의 공고번호 중복 여부와 내용을 비교한다.
# 행의 순서가 달라져도 동일 공고와 동일 내용이면 같다고 판단한다.
def row_index(rows):
    indexed = {}

    for row in rows:
        key = row["desertion_no"]

        if key in indexed:
            raise ValueError(f"Duplicate Silver key: {key}")

        indexed[key] = row

    return indexed


def same_rows(left, right):
    return row_index(left) == row_index(right)


# 7. 테스트 테이블이 비어 있다면 10월 28일 데이터 87건을 먼저 적재한다.
# 이전 실행에서 일부 작업이 완료된 경우에도 안전하게 다시 시작할 수 있도록
# 기존 상태가 예상한 데이터인지 확인한다.
existing = table.scan().to_arrow().to_pylist()

if not existing:
    table.append(previous_arrow)
    print("INITIAL_APPENDED:", len(previous_rows))

elif same_rows(existing, previous_rows):
    print("INITIAL_ALREADY_MATCHED:", len(existing))

elif same_rows(existing, current_rows):
    print("FINAL_ALREADY_MATCHED:", len(existing))

else:
    raise RuntimeError("Unexpected existing test data; no overwrite performed")


# 8. 10월 30일 데이터 115건을 UPSERT한다.
# INSERT: 공고번호가 없는 신규 공고는 추가한다.
# UPDATE: 공고번호가 같지만 다른 컬럼 값이 바뀌었으면 갱신한다.
#
# join_cols는 두 데이터를 비교할 기준 키를 지정한다.
# updTm은 별도 변경 판단 근거로 사용하지 않는다.
# 다만 현재 정제 결과에 보존돼 있으므로 값이 바뀌면 일반 컬럼 변경으로
# 감지될 수 있다. 운영용 비교에서는 updTm 제외 규칙을 추가해야 한다.
table = catalog.load_table(test_name)

result = table.upsert(
    current_arrow,
    join_cols=["desertion_no"],
    when_matched_update_all=True,
    when_not_matched_insert_all=True,
)

print("UPSERT_INSERTED:", result.rows_inserted)
print("UPSERT_UPDATED:", result.rows_updated)


# 9. Iceberg에서 데이터를 다시 읽어 최종 115건과 전체 내용이 일치하는지 확인한다.
verified = catalog.load_table(test_name).scan().to_arrow().to_pylist()

if len(verified) != 115:
    raise RuntimeError(f"Incorrect final count: {len(verified)}")

if not same_rows(verified, current_rows):
    raise RuntimeError("Saved data does not match current snapshot")

print("VERIFIED_FINAL_ROWS:", len(verified))


# 10. 같은 데이터를 다시 UPSERT한다.
# 이미 반영된 공고가 재실행으로 중복 추가되거나 불필요하게 수정되는지 검사한다.
rerun = catalog.load_table(test_name).upsert(
    current_arrow,
    join_cols=["desertion_no"],
    when_matched_update_all=True,
    when_not_matched_insert_all=True,
)

print("RERUN_INSERTED:", rerun.rows_inserted)
print("RERUN_UPDATED:", rerun.rows_updated)

if rerun.rows_inserted != 0 or rerun.rows_updated != 0:
    raise RuntimeError("Rerun was not idempotent")

final_rows = catalog.load_table(test_name).scan().to_arrow().to_pylist()

if not same_rows(final_rows, current_rows):
    raise RuntimeError("Final content verification failed")

print("FINAL_ROWS:", len(final_rows))
print("RESULT: PASS")
