"""검증된 Bronze 증분을 Silver Iceberg에 반영하는 로컬 통합 테스트."""

import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path

import pyarrow as pa
from pyiceberg.catalog import load_catalog

from rebom.storage.silver.load_bronze import load_bronze_run
from rebom.storage.silver.transform_notice import transform_notice


# 1. A팀이 Git에 제공한 두 시점의 JSON 샘플을 읽는다.
# 두 파일은 테스트 입력이며 실제 Bronze 배치 파일은 아니다.
sample_dir = Path("data/sample/incremental")

with (sample_dir / "t0_2026-10-28.json").open(encoding="utf-8-sig") as f:
    previous_raw = json.load(f)

with (sample_dir / "truth_2026-10-30.json").open(encoding="utf-8-sig") as f:
    current_raw = json.load(f)


def index_by_id(rows):
    # 공고번호는 비즈니스 키다.
    # 동일한 키가 중복되면 임의로 덮어쓰지 않고 중단한다.
    indexed = {}

    for row in rows:
        key = str(row.get("desertionNo") or "").strip()

        if not key or key in indexed:
            raise ValueError(f"유효하지 않거나 중복된 공고번호: {key}")

        indexed[key] = row

    return indexed


previous = index_by_id(previous_raw)
current = index_by_id(current_raw)

if len(previous) != 87 or len(current) != 115:
    raise RuntimeError("원본 샘플 건수가 예상과 다릅니다.")

# 현재 테스트에는 사라진 공고가 없어야 한다.
# 부분 증분만으로 공고가 사라졌다고 판단해서는 안 된다.
if set(previous) - set(current):
    raise RuntimeError("스냅샷에서 사라진 공고가 있습니다.")


# 2. 기존 Silver 정제 결과를 비교한다.
# 이번 테스트에서는 실제 Silver 값이 달라진 공고만 증분으로 만든다.
#
# 이전에 정제한 값과 현재 값이 같다면 다시 저장할 필요가 없다.
# 단, 운영 정책상 updTm 제외 여부는 아직 별도로 결정해야 한다.
previous_silver = {
    key: transform_notice(raw)
    for key, raw in previous.items()
}

current_silver = {
    key: transform_notice(raw)
    for key, raw in current.items()
}

delta_raw = [
    raw
    for key, raw in current.items()
    if key not in previous_silver
    or previous_silver[key] != current_silver[key]
]

delta_ids = {str(raw["desertionNo"]) for raw in delta_raw}
unchanged_ids = set(previous) - delta_ids

if not unchanged_ids:
    raise RuntimeError("기존 공고 보존을 검증할 대상이 없습니다.")

print("BASELINE:", len(previous))
print("EXPECTED_CURRENT:", len(current))
print("DELTA_RECORDS:", len(delta_raw))
print("UNCHANGED_NOT_IN_DELTA:", len(unchanged_ids))


# 3. 실제 A팀과 동일한 구조의 임시 Bronze 실행 폴더를 만든다.
#
# 테스트에서는 updated에만 변경분을 넣고 다른 목록은 비워 둔다.
# 따라서 출처별 충돌 우선순위를 임의로 정하지 않아도 된다.
run_id = "20261102T160000-abcdef"
fetched_at = "2026-11-02T16:00:00+09:00"

with tempfile.TemporaryDirectory(prefix="rebom_verified_bronze_") as temp:
    run_dir = Path(temp) / "dt=2026-11-02" / f"run_id={run_id}"
    run_dir.mkdir(parents=True)

    kinds = {
        "updated": delta_raw,
        "list_notice": [],
        "list_protect": [],
        "events": [],
    }

    files = []

    for kind, records in kinds.items():
        path = run_dir / f"{kind}.jsonl.gz"

        with gzip.open(path, "wt", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        files.append({
            "kind": kind,
            "file": path.name,
            "record_count": len(records),
            "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
        })

    # 정상 완료된 수집 구간을 모의로 구성한다.
    # 이것은 A팀의 실제 수집 완전성을 증명하는 값이 아니다.
    manifest = {
        "run_id": run_id,
        "fetched_at": fetched_at,
        "status": "ok",
        "files": files,
        "partitions": [
            {
                "kind": "updated",
                "result_code": "00",
                "start_date": "2026-10-28",
                "end_date": "2026-10-30",
                "total_count": len(delta_raw),
                "received_count": len(delta_raw),
                "complete": True,
                "reason": "complete",
                "reprocess_ids": [],
            }
        ],
        "reprocess_ids": [],
    }

    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )

    # 4. 바로 위에서 만든 파일을 다시 열어 무결성을 검증한다.
    # 이후에는 원래 메모리에 있던 delta_raw가 아니라
    # 검증을 통과한 로더의 반환값만 사용한다.
    verified = load_bronze_run(manifest_path)

    verified_delta = verified["rows_by_kind"]["updated"]

    if len(verified_delta) != len(delta_raw):
        raise RuntimeError("Bronze 증분 건수가 다릅니다.")

    if verified["incomplete_partitions"]:
        raise RuntimeError("테스트 manifest에 불완전 구간이 있습니다.")

    print("MANIFEST_VALIDATION: PASS")
    print("CHECKSUM_VALIDATION: PASS")

    # 5. 검증된 Bronze 공고에만 기존 B팀 정제 함수를 적용한다.
    delta_silver = [
        transform_notice(raw, source_type="updated")
        for raw in verified_delta
    ]


# 6. 로컬 Iceberg Catalog에 연결한다.
# 운영 Silver와 기존 테스트 테이블은 수정하지 않는다.
base = Path(os.environ["LOCALAPPDATA"]) / "rebom-iceberg"

catalog = load_catalog(
    "rebom_local",
    type="sql",
    uri=f"sqlite:///{(base / 'catalog.db').as_posix()}",
    warehouse=str(base / "warehouse"),
)

source_name = "rebom_silver.silver_animal_notice"
test_name = "rebom_silver.silver_animal_notice_verified_bronze_test"

source_table = catalog.load_table(source_name)

if not catalog.table_exists(test_name):
    catalog.create_table(
        test_name,
        schema=source_table.schema(),
    )
    print("CREATED_TEST_TABLE:", test_name)

table = catalog.load_table(test_name)

if table.schema() != source_table.schema():
    raise RuntimeError("테스트 테이블의 스키마가 다릅니다.")

arrow_schema = table.schema().as_arrow()


def silver_index(rows):
    indexed = {}

    for row in rows:
        key = row["desertion_no"]

        if key in indexed:
            raise ValueError(f"Silver 중복 키: {key}")

        indexed[key] = row

    return indexed


baseline_rows = list(previous_silver.values())
expected_rows = list(current_silver.values())

baseline_arrow = pa.Table.from_pylist(
    baseline_rows,
    schema=arrow_schema,
)

delta_arrow = pa.Table.from_pylist(
    delta_silver,
    schema=arrow_schema,
)

existing = table.scan().to_arrow().to_pylist()

# 7. 최초 실행에만 기준 공고 87건을 저장한다.
# 이미 실행한 경우에는 예상 데이터인지 확인하고 이어간다.
if not existing:
    table.append(baseline_arrow)
    print("BASELINE_INSERTED:", len(baseline_rows))

elif silver_index(existing) == silver_index(baseline_rows):
    print("BASELINE_ALREADY_EXISTS:", len(existing))

elif silver_index(existing) == silver_index(expected_rows):
    print("FINAL_ALREADY_EXISTS:", len(existing))

else:
    raise RuntimeError("예상하지 못한 테스트 데이터가 존재합니다.")


# 8. Bronze에 있는 증분 공고만 UPSERT한다.
# 전체 스냅샷을 교체하지 않기 때문에 미수집 기존 공고는 유지된다.
result = catalog.load_table(test_name).upsert(
    delta_arrow,
    join_cols=["desertion_no"],
    when_matched_update_all=True,
    when_not_matched_insert_all=True,
)

print("UPSERT_INSERTED:", result.rows_inserted)
print("UPSERT_UPDATED:", result.rows_updated)


# 9. 기존 87건 + 신규 및 변경분이 전체 115건과 동일한지 확인한다.
stored = catalog.load_table(test_name).scan().to_arrow().to_pylist()

if silver_index(stored) != silver_index(expected_rows):
    raise RuntimeError("Iceberg 최종 공고 내용이 다릅니다.")

if not unchanged_ids.issubset(silver_index(stored)):
    raise RuntimeError("증분에 없던 기존 공고가 삭제됐습니다.")

print("VERIFIED_FINAL_ROWS:", len(stored))
print("UNCHANGED_PRESERVED:", len(unchanged_ids))


# 10. 동일한 증분을 한 번 더 처리한다.
# 이미 저장된 데이터라면 추가 INSERT와 UPDATE가 없어야 한다.
rerun = catalog.load_table(test_name).upsert(
    delta_arrow,
    join_cols=["desertion_no"],
    when_matched_update_all=True,
    when_not_matched_insert_all=True,
)

print("RERUN_INSERTED:", rerun.rows_inserted)
print("RERUN_UPDATED:", rerun.rows_updated)

if rerun.rows_inserted or rerun.rows_updated:
    raise RuntimeError("동일 증분 재실행이 멱등하지 않습니다.")

final_rows = catalog.load_table(test_name).scan().to_arrow().to_pylist()

if silver_index(final_rows) != silver_index(expected_rows):
    raise RuntimeError("재실행 후 최종 데이터가 달라졌습니다.")

print("FINAL_ROWS:", len(final_rows))
print("PRODUCTION_SILVER: NOT_MODIFIED")
print("ACTUAL_A_TEAM_BRONZE: NOT_TESTED")
print("STATUS_HISTORY: NOT_TESTED")
print("RESULT: PASS")
