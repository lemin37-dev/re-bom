import json
import os
from pathlib import Path

import pyarrow as pa
from pyiceberg.catalog import load_catalog

from rebom.storage.silver.transform_notice import transform_notice
from rebom.storage.silver.transform_image import transform_images
from rebom.storage.silver.transform_shelter import transform_shelters


# 현재 작업 폴더의 샘플 JSON을 읽는다.
# A팀의 실제 S3 Bronze 데이터 대신 사용하는 로컬 테스트 입력이다.
source = Path("data/sample/rescue_sample.json")
with source.open(encoding="utf-8-sig") as file:
    records = json.load(file)

if not isinstance(records, list):
    raise TypeError("Sample data must be a JSON array")

# 기존 B팀 정제 함수를 재사용한다.
# 원본 필드를 Silver 표준 컬럼으로 변환한다.
notices = [transform_notice(raw) for raw in records]

# 공고 한 건에 여러 사진이 있으므로 이미지 테이블은 여러 행을 만든다.
images = [
    image
    for raw in records
    for image in transform_images(raw)
]

# 동일한 보호소는 care_reg_no 기준으로 묶는다.
# 보호소 정보가 충돌하면 임의로 덮어쓰지 않고 별도로 기록한다.
shelters, conflicts, missing_keys = transform_shelters(records)

# 테이블에 기록하기 전에 기본 데이터 정합성을 검사한다.
# 문제가 있는 데이터를 저장한 뒤 발견하는 상황을 줄이기 위한 검사다.
notice_ids = [row["desertion_no"] for row in notices]
shelter_ids = [row["care_reg_no"] for row in shelters]

if len(notice_ids) != len(set(notice_ids)):
    raise ValueError("Duplicate desertion_no in sample")

if len(shelter_ids) != len(set(shelter_ids)):
    raise ValueError("Duplicate care_reg_no in sample")

if any(image["desertion_no"] not in set(notice_ids) for image in images):
    raise ValueError("Image refers to an unknown animal")

print("INPUT:", len(records))
print("NOTICE:", len(notices))
print("IMAGE:", len(images))
print("SHELTER:", len(shelters))
print("SHELTER_CONFLICTS:", len(conflicts))
print("SHELTER_MISSING_KEYS:", len(missing_keys))

# 앞 단계에서 생성한 SQLite Catalog와 로컬 Warehouse를 재사용한다.
# 새 카탈로그를 만들거나 기존 테이블을 삭제하지 않는다.
base = Path(os.environ["LOCALAPPDATA"]) / "rebom-iceberg"

catalog = load_catalog(
    "rebom_local",
    type="sql",
    uri=f"sqlite:///{(base / 'catalog.db').as_posix()}",
    warehouse=str(base / "warehouse"),
)

# 테이블별로 저장할 데이터를 연결한다.
# 상태 이력은 두 수집 시점의 비교가 필요하므로 이번 단계에서 제외한다.
datasets = {
    "silver_animal_notice": notices,
    "silver_animal_image": images,
    "silver_shelter": shelters,
}

# 저장에 앞서 세 테이블의 스키마 호환성을 모두 검사한다.
# 일부만 적재한 뒤 타입 오류를 발견하는 상황을 줄인다.
prepared = {}

for name, rows in datasets.items():
    table = catalog.load_table(f"rebom_silver.{name}")

    # Iceberg에 등록된 컬럼명과 타입을 PyArrow 스키마로 변환한다.
    arrow_schema = table.schema().as_arrow()

    # 정제된 Python dict 목록을 컬럼 기반 PyArrow 테이블로 변환한다.
    arrow_data = pa.Table.from_pylist(rows, schema=arrow_schema)

    if arrow_data.num_rows != len(rows):
        raise ValueError(f"Prepared row count mismatch: {name}")

    # 이미 데이터가 적재된 테이블은 무조건 추가하지 않는다.
    # append는 기본적으로 중복 여부를 자동 검사하지 않기 때문이다.
    existing = table.scan().to_arrow()

    if existing.num_rows > 0:
        if existing.num_rows == len(rows) and existing.equals(arrow_data):
            print(f"ALREADY_MATCHED: {name} ({len(rows)} rows)")
            continue

        raise RuntimeError(
            f"Table already contains different data: {name}. "
            "Manual review required before writing."
        )

    prepared[name] = arrow_data
    print(f"PREPARED: {name} ({arrow_data.num_rows} rows)")

# 각 테이블에 데이터를 기록한다.
# Iceberg는 데이터 파일과 Snapshot 메타데이터를 함께 관리한다.
# 여러 테이블 전체를 하나의 원자적 트랜잭션으로 처리하는 코드는 아니다.
for name, arrow_data in prepared.items():
    table = catalog.load_table(f"rebom_silver.{name}")

    # 실제 데이터를 Parquet 파일로 기록하고 Iceberg Snapshot을 갱신한다.
    table.append(arrow_data)

    print(f"APPENDED: {name} ({arrow_data.num_rows} rows)")

# 다시 카탈로그에서 테이블을 열어 실제 저장 결과를 검증한다.
# Python 메모리에 있던 데이터가 아니라 Iceberg에 저장된 데이터를 읽는다.
for name, expected_rows in datasets.items():
    table = catalog.load_table(f"rebom_silver.{name}")
    actual = table.scan().to_arrow()

    if actual.num_rows != len(expected_rows):
        raise RuntimeError(
            f"Row count mismatch: {name}, "
            f"expected={len(expected_rows)}, actual={actual.num_rows}"
        )

    print(f"VERIFIED: {name} ({actual.num_rows} rows)")

# 이력 테이블은 만들어져 있지만 이번 단계에서 적재하지 않았다.
history = catalog.load_table(
    "rebom_silver.silver_animal_status_history"
)

print("HISTORY_ROWS:", history.scan().to_arrow().num_rows)
print("RESULT: PASS")
