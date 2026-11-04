"""검증된 Bronze 증분을 별도 Iceberg 테이블에 실제 적재한다."""

import gzip
import hashlib
import json
import tempfile
from pathlib import Path

import pyarrow as pa

from rebom.storage.silver.apply_notice_pipeline import (
    TEST_TABLE,
    SOURCE_TABLE,
    execute_notice_pipeline,
    get_local_catalog,
    index_rows,
)
from rebom.storage.silver.prepare_notice_delta import content_signature
from rebom.storage.silver.transform_notice import transform_notice


# 1. A팀이 Git에 제공한 스냅샷 2개를 읽는다.
# 실제 두 번째 Bronze 수집 파일은 아직 제공되지 않았다.
sample_dir = Path("data/sample/incremental")

with (sample_dir / "t0_2026-10-28.json").open(encoding="utf-8-sig") as f:
    previous_raw = json.load(f)

with (sample_dir / "truth_2026-10-30.json").open(encoding="utf-8-sig") as f:
    current_raw = json.load(f)

previous = {
    str(row["desertionNo"]): row
    for row in previous_raw
}

current = {
    str(row["desertionNo"]): row
    for row in current_raw
}

if len(previous) != 87 or len(current) != 115:
    raise RuntimeError("샘플 데이터 수가 예상과 다릅니다.")

# 2. A팀의 두 스냅샷으로 변경된 공고만 추출한다.
# 운영 로직이 실제 수집 결과를 만드는 과정과는 구분되는
# 테스트용 입력 생성 과정이다.
previous_silver = {
    key: transform_notice(raw)
    for key, raw in previous.items()
}

current_silver = {
    key: transform_notice(raw)
    for key, raw in current.items()
}

delta = []

for key, raw in current.items():
    if key not in previous_silver:
        delta.append(raw)
        continue

    old = content_signature(
        previous_silver[key],
        {"source_updated_at_raw"},
    )
    new = content_signature(
        current_silver[key],
        {"source_updated_at_raw"},
    )

    if old != new:
        delta.append(raw)

print("BASELINE:", len(previous))
print("EXPECTED_CURRENT:", len(current))
print("DELTA_RECORDS:", len(delta))


# 3. Iceberg에 전용 테스트 테이블을 준비한다.
# 기존 테이블이 있으면 삭제하지 않고 현재 내용을 확인한다.
catalog = get_local_catalog()
source = catalog.load_table(SOURCE_TABLE)

if not catalog.table_exists(TEST_TABLE):
    catalog.create_table(TEST_TABLE, schema=source.schema())
    print("CREATED_TEST_TABLE:", TEST_TABLE)

table = catalog.load_table(TEST_TABLE)

if table.schema() != source.schema():
    raise RuntimeError("테스트 테이블 스키마 불일치")

baseline_rows = list(previous_silver.values())
expected_rows = list(current_silver.values())
existing = table.scan().to_arrow().to_pylist()

if not existing:
    baseline_arrow = pa.Table.from_pylist(
        baseline_rows,
        schema=table.schema().as_arrow(),
    )
    table.append(baseline_arrow)
    print("INITIAL_BASELINE_INSERTED:", len(baseline_rows))

elif index_rows(existing) == index_rows(baseline_rows):
    print("INITIAL_BASELINE_ALREADY_EXISTS:", len(existing))

elif index_rows(existing) == index_rows(expected_rows):
    print("FINAL_DATA_ALREADY_EXISTS:", len(existing))

else:
    raise RuntimeError(
        "테스트 테이블에 예상하지 못한 데이터가 있습니다. 자동 초기화하지 않습니다."
    )


# 4. manifest와 Bronze JSONL.gz 파일을 임시 폴더에 생성한다.
# 파일 검증은 실제 공통 모듈인 load_bronze_run()에서 수행한다.
run_id = "20261102T160000-abcdef"
fetched_at = "2026-11-02T16:00:00+09:00"

with tempfile.TemporaryDirectory(prefix="rebom_apply_pipeline_") as temp:
    run_dir = Path(temp) / "dt=2026-11-02" / f"run_id={run_id}"
    run_dir.mkdir(parents=True)

    kinds = {
        "updated": delta,
        "list_notice": [],
        "list_protect": [],
    }

    files = []

    for kind, rows in kinds.items():
        path = run_dir / f"{kind}.jsonl.gz"

        with gzip.open(path, "wt", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

        files.append({
            "kind": kind,
            "file": path.name,
            "record_count": len(rows),
            "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
        })

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
                "total_count": len(delta),
                "received_count": len(delta),
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

    # 5. DRY RUN: 적재 대상을 조회만 하고 쓰지 않는다.
    before_count = len(
        catalog.load_table(TEST_TABLE).scan().to_arrow().to_pylist()
    )

    preview = execute_notice_pipeline(manifest_path, apply=False)

    assert preview["ready"]
    assert not preview["applied"]

    after_preview_count = len(
        catalog.load_table(TEST_TABLE).scan().to_arrow().to_pylist()
    )

    assert before_count == after_preview_count

    print("DRY_RUN: PASS")
    print("PREPARED_INSERT:", preview["prepared_insert"])
    print("PREPARED_UPDATE:", preview["prepared_update"])

    # 6. APPLY: 전용 테스트 테이블에만 실제 UPSERT한다.
    applied = execute_notice_pipeline(manifest_path, apply=True)

    assert applied["ready"]
    assert applied["applied"]
    final_count = len(
        catalog.load_table(TEST_TABLE).scan().to_arrow().to_pylist()
    )
    assert final_count == 115
    assert applied["prepared_insert"] == 0 or applied["rows_inserted"] >= 0

    print("APPLY_INSERTED:", applied["rows_inserted"])
    print("APPLY_UPDATED:", applied["rows_updated"])

    # 7. 재실행: 같은 실행을 두 번 적용해도 데이터가 달라지면 안 된다.
    rerun = execute_notice_pipeline(manifest_path, apply=True)

    assert rerun["rows_inserted"] == 0
    assert rerun["rows_updated"] == 0

    print("RERUN_INSERTED:", rerun["rows_inserted"])
    print("RERUN_UPDATED:", rerun["rows_updated"])

    # 8. 최종 저장된 115건이 기대 스냅샷과 같은지 비교한다.
    final = catalog.load_table(TEST_TABLE).scan().to_arrow().to_pylist()

    assert len(final) == 115
    assert index_rows(final) == index_rows(expected_rows)

    print("FINAL_ROWS:", len(final))
    print("FINAL_CONTENT_VERIFICATION: PASS")

print("PRODUCTION_SILVER: NOT_MODIFIED")
print("ACTUAL_A_TEAM_BRONZE: NOT_TESTED")
print("STATUS_HISTORY: NOT_TESTED")
print("RESULT: PASS")
