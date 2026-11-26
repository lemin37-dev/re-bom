import json
import os
from collections import Counter
from pathlib import Path

import pyarrow as pa
from pyiceberg.catalog import load_catalog

from rebom.storage.silver.transform_status_history import transform_status_history


# 이전 수집 데이터와 이후 수집 데이터를 각각 읽는다.
# 상태 이력은 한 시점의 데이터만으로는 변경 여부를 판단할 수 없다.
sample_dir = Path("data/sample/incremental")

with (sample_dir / "t0_2026-10-28.json").open(encoding="utf-8-sig") as f:
    previous = json.load(f)

with (sample_dir / "truth_2026-10-30.json").open(encoding="utf-8-sig") as f:
    current = json.load(f)

# 입력 파일이 공고 목록인지 확인한다.
# 예상하지 못한 형식을 잘못 적재하지 않기 위한 검사다.
if not isinstance(previous, list) or not isinstance(current, list):
    raise TypeError("Both snapshot files must contain JSON arrays")

# 기존 B팀 코드를 이용해 두 시점의 상태 차이를 계산한다.
# 최초 관측은 INITIAL, 실제 상태 변경은 CHANGED로 기록한다.
# 종료 확인 목록을 전달하지 않았으므로 미관측 공고를 임의 종료 처리하지 않는다.
events, stats = transform_status_history(
    previous_records=previous,
    current_records=current,
    previous_observed_on="2026-10-28",
    current_observed_on="2026-10-30",
)

print("PREVIOUS:", len(previous))
print("CURRENT:", len(current))
print("HISTORY_EVENTS:", len(events))
print("STATISTICS:", stats)

# 기존 테스트에서 확인한 이력 159건과 비교한다.
# 결과가 달라졌다면 데이터를 기록하기 전에 확인하도록 중단한다.
if len(events) != 159:
    raise RuntimeError(
        f"Unexpected history count: {len(events)}"
    )

# 이전에 생성한 Iceberg Catalog에 연결한다.
base = Path(os.environ["LOCALAPPDATA"]) / "rebom-iceberg"

catalog = load_catalog(
    "rebom_local",
    type="sql",
    uri=f"sqlite:///{(base / 'catalog.db').as_posix()}",
    warehouse=str(base / "warehouse"),
)

# 상태 이력 테이블의 실제 스키마를 읽는다.
# 임의로 컬럼을 새로 정의하지 않고 기존 Iceberg 스키마에 맞춘다.
table = catalog.load_table(
    "rebom_silver.silver_animal_status_history"
)

arrow_schema = table.schema().as_arrow()
arrow_data = pa.Table.from_pylist(events, schema=arrow_schema)

# 행의 순서가 달라도 내용이 같은지 비교할 수 있도록 정규화한다.
# 이력 데이터는 동일 이벤트를 반복 적재하지 않는 것이 중요하다.
def row_counts(rows):
    return Counter(
        json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
        for row in rows
    )

# 기존에 저장된 이력이 있는지 확인한다.
existing = table.scan().to_arrow()

if existing.num_rows > 0:
    if row_counts(existing.to_pylist()) == row_counts(arrow_data.to_pylist()):
        print("ALREADY_MATCHED: history data")
    else:
        raise RuntimeError(
            "History table contains different data. "
            "Manual review required."
        )
else:
    # 비어 있는 상태 이력 테이블에 실제 이벤트를 적재한다.
    # append는 자동 중복 제거 기능이 아니므로 사전 검증이 필요하다.
    table.append(arrow_data)
    print("APPENDED:", arrow_data.num_rows)

# 카탈로그에서 테이블을 새로 불러와 저장된 이력을 검증한다.
verified_table = catalog.load_table(
    "rebom_silver.silver_animal_status_history"
)
stored = verified_table.scan().to_arrow()

if row_counts(stored.to_pylist()) != row_counts(arrow_data.to_pylist()):
    raise RuntimeError("History verification failed")

print("VERIFIED_HISTORY:", stored.num_rows)
print("RESULT: PASS")
