"""Bronze JSONL.gz에서 Iceberg Silver UPSERT까지 연결하는 로컬 통합 테스트."""

import gzip
import json
import os
import tempfile
from pathlib import Path

import pyarrow as pa
from pyiceberg.catalog import load_catalog

from rebom.storage.silver.transform_notice import transform_notice


# 1. A팀의 기존 증분 샘플을 읽는다.
# 실제 수집 파일은 아직 없으므로 JSON 배열 데이터를 JSONL.gz로 변환해 시험한다.
sample_dir = Path("data/sample/incremental")

with (sample_dir / "t0_2026-10-28.json").open(encoding="utf-8-sig") as f:
    previous = json.load(f)

with (sample_dir / "truth_2026-10-30.json").open(encoding="utf-8-sig") as f:
    current = json.load(f)

if len(previous) != 87 or len(current) != 115:
    raise RuntimeError("예상하지 못한 샘플 건수입니다.")


# 2. JSONL.gz 파일 입출력 함수를 정의한다.
# JSONL은 한 줄에 JSON 객체 하나를 저장한다.
# gzip은 이 텍스트 파일을 압축하는 방식이다.
def write_jsonl_gz(path, records):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl_gz(path):
    records = []

    with gzip.open(path, "rt", encoding="utf-8-sig") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"{path.name}:{line_number} JSON 오류"
                ) from e

            if not isinstance(record, dict):
                raise ValueError("JSONL의 각 줄은 객체여야 합니다.")

            records.append(record)

    return records


# 3. 공고번호를 기준으로 중복을 확인하고 병합한다.
# 현재 테스트에서는 updated 파일에 최종 정답이 있다는 조건을 사용한다.
#
# 주의: 이 규칙은 모의 데이터 전용이다.
# 실제 운영에서는 수집 출처, 변경 내용, 검증된 종료 이벤트를 함께 판단한다.
def merge_records(file_map):
    result = {}
    duplicate_count = 0

    for kind in ("list_notice", "list_protect", "updated"):
        rows = read_jsonl_gz(file_map[kind])
        print(f"READ_{kind.upper()}:", len(rows))

        for raw in rows:
            key = str(raw.get("desertionNo") or "").strip()

            if not key:
                raise ValueError("desertionNo가 없습니다.")

            if key in result:
                duplicate_count += 1

            result[key] = (kind, raw)

    return result, duplicate_count


# 4. Bronze 형식의 임시 파일을 만든다.
# 운영용 data/bronze 폴더에는 파일을 생성하지 않는다.
with tempfile.TemporaryDirectory(prefix="rebom_integration_") as tmp:
    temp = Path(tmp)

    baseline_file = temp / "baseline.jsonl.gz"
    write_jsonl_gz(baseline_file, previous)

    file_map = {
        "list_notice": temp / "list_notice.jsonl.gz",
        "list_protect": temp / "list_protect.jsonl.gz",
        "updated": temp / "updated.jsonl.gz",
    }

    write_jsonl_gz(file_map["list_notice"], previous)
    write_jsonl_gz(file_map["list_protect"], current[:20])
    write_jsonl_gz(file_map["updated"], current)

    # 최초 87건과 최신 115건을 각각 Bronze 압축 파일에서 다시 읽는다.
    baseline_raw = read_jsonl_gz(baseline_file)
    merged, duplicates = merge_records(file_map)


# 5. 실제 Silver 정제 함수를 호출한다.
# 별도의 테스트용 정제 규칙을 만들지 않는다.
baseline_rows = [
    transform_notice(raw)
    for raw in baseline_raw
]

current_rows = [
    transform_notice(raw, source_type=kind)
    for kind, raw in merged.values()
]

print("BRONZE_DUPLICATES:", duplicates)
print("BRONZE_UNIQUE:", len(merged))
print("SILVER_PREPARED:", len(current_rows))


# 6. 최종 정제 결과를 정답 스냅샷과 비교한다.
# 데이터 순서와 상관없이 desertion_no별 값이 동일해야 한다.
def index_rows(rows):
    result = {}

    for row in rows:
        key = row["desertion_no"]

        if key in result:
            raise ValueError(f"Silver 공고번호 중복: {key}")

        result[key] = row

    return result


expected_rows = [transform_notice(raw) for raw in current]

if index_rows(current_rows) != index_rows(expected_rows):
    raise RuntimeError("Bronze 병합 결과와 정답 데이터가 다릅니다.")

if len(current_rows) != 115:
    raise RuntimeError("최종 공고 수가 115건이 아닙니다.")


# 7. 로컬 Iceberg Catalog에 연결한다.
# 기존 Silver 테이블과 분리된 통합 테스트용 테이블을 사용한다.
base = Path(os.environ["LOCALAPPDATA"]) / "rebom-iceberg"

catalog = load_catalog(
    "rebom_local",
    type="sql",
    uri=f"sqlite:///{(base / 'catalog.db').as_posix()}",
    warehouse=str(base / "warehouse"),
)

source_name = "rebom_silver.silver_animal_notice"
test_name = "rebom_silver.silver_animal_notice_bronze_integration_test"

source_table = catalog.load_table(source_name)

if not catalog.table_exists(test_name):
    catalog.create_table(
        test_name,
        schema=source_table.schema(),
    )
    print("CREATED_TABLE:", test_name)

table = catalog.load_table(test_name)
schema = table.schema().as_arrow()

baseline_arrow = pa.Table.from_pylist(baseline_rows, schema=schema)
current_arrow = pa.Table.from_pylist(current_rows, schema=schema)


# 8. 테스트 테이블에 최초 87건을 적재한다.
# 이전 실행의 데이터가 남아 있으면 내용을 확인한다.
# 예상하지 못한 데이터가 있다면 덮어쓰지 않고 중단한다.
existing = table.scan().to_arrow().to_pylist()

if not existing:
    table.append(baseline_arrow)
    print("INITIAL_APPENDED:", len(baseline_rows))

elif index_rows(existing) == index_rows(baseline_rows):
    print("INITIAL_ALREADY_MATCHED:", len(existing))

elif index_rows(existing) == index_rows(current_rows):
    print("FINAL_ALREADY_MATCHED:", len(existing))

else:
    raise RuntimeError("테스트 테이블에 예상하지 못한 데이터가 있습니다.")


# 9. 최신 공고 115건을 UPSERT한다.
# 동일 desertion_no는 기존 행을 비교하고, 신규 공고는 추가한다.
#
# UPSERT는 INSERT와 UPDATE를 결합한 처리 방식이다.
table = catalog.load_table(test_name)

result = table.upsert(
    current_arrow,
    join_cols=["desertion_no"],
    when_matched_update_all=True,
    when_not_matched_insert_all=True,
)

print("UPSERT_INSERTED:", result.rows_inserted)
print("UPSERT_UPDATED:", result.rows_updated)


# 10. Iceberg에서 실제 저장 결과를 다시 조회한다.
# 단순히 실행 성공 여부만 보는 게 아니라 전체 내용까지 비교한다.
stored = catalog.load_table(test_name).scan().to_arrow().to_pylist()

if len(stored) != 115:
    raise RuntimeError(f"Iceberg 최종 건수 오류: {len(stored)}")

if index_rows(stored) != index_rows(current_rows):
    raise RuntimeError("Iceberg 최종 내용 불일치")

print("VERIFIED_ICEBERG_ROWS:", len(stored))


# 11. 동일 데이터를 한 번 더 적재한다.
# 재실행해도 중복 INSERT나 불필요한 UPDATE가 발생하면 안 된다.
rerun = catalog.load_table(test_name).upsert(
    current_arrow,
    join_cols=["desertion_no"],
    when_matched_update_all=True,
    when_not_matched_insert_all=True,
)

print("RERUN_INSERTED:", rerun.rows_inserted)
print("RERUN_UPDATED:", rerun.rows_updated)

if rerun.rows_inserted != 0 or rerun.rows_updated != 0:
    raise RuntimeError("동일 데이터 재실행 멱등성 검증 실패")

final = catalog.load_table(test_name).scan().to_arrow().to_pylist()

if index_rows(final) != index_rows(current_rows):
    raise RuntimeError("재실행 후 데이터가 변경됐습니다.")

print("FINAL_ICEBERG_ROWS:", len(final))
print("ACTUAL_BRONZE_FILES:", "NOT_TESTED")
print("MANIFEST_CHECKSUM:", "NOT_TESTED")
print("STATUS_EVENTS:", "NOT_TESTED")
print("RESULT: PASS")
