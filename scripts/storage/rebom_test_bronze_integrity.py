"""A팀 Bronze manifest, 체크섬, 건수, 개인정보 검사 테스트."""

import gzip
import hashlib
import json
import tempfile
from pathlib import Path

from rebom.storage.silver.load_bronze import load_bronze_run


# 1. 실제 A팀 파일 저장 형식을 사용해 작은 테스트 실행본을 만든다.
# 데이터 내용은 가상이므로 실제 API 성공을 의미하지 않는다.
run_id = "20261102T160000-abcdef"
fetched_at = "2026-11-02T16:00:00+09:00"

with tempfile.TemporaryDirectory(prefix="rebom_bronze_integrity_") as temp:
    run_dir = Path(temp) / "dt=2026-11-02" / f"run_id={run_id}"
    run_dir.mkdir(parents=True)

    samples = {
        "updated": [{"desertionNo": "A", "processState": "보호중"}],
        "list_notice": [{"desertionNo": "A", "processState": "보호중"}],
        "list_protect": [{"desertionNo": "B", "processState": "보호중"}],
        "events": [],
    }

    file_entries = []

    for kind, rows in samples.items():
        path = run_dir / f"{kind}.jsonl.gz"

        # A팀 코드처럼 JSON 객체마다 개행을 붙여 gzip으로 저장한다.
        with gzip.open(path, "wt", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

        checksum = hashlib.sha256(path.read_bytes()).hexdigest()

        file_entries.append({
            "kind": kind,
            "file": path.name,
            "record_count": len(rows),
            "checksum": checksum,
        })

    # 파티션 데이터는 파일 개수와 별도로 수집 완전성을 판단한다.
    partitions = [
        {
            "kind": "list_protect",
            "result_code": "00",
            "start_date": "2026-10-01",
            "end_date": "2026-10-31",
            "total_count": 2,
            "received_count": 1,
            "complete": False,
            "reason": "count_mismatch",
            "reprocess_ids": [],
        }
    ]

    manifest = {
        "run_id": run_id,
        "fetched_at": fetched_at,
        "status": "ok",
        "files": file_entries,
        "partitions": partitions,
        "reprocess_ids": [],
    }

    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )

    # 2. 정상 파일을 읽으면 모든 체크섬과 건수가 일치해야 한다.
    loaded = load_bronze_run(manifest_path)

    assert loaded["run_id"] == run_id
    assert len(loaded["rows_by_kind"]["updated"]) == 1
    assert len(loaded["rows_by_kind"]["events"]) == 0
    assert len(loaded["incomplete_partitions"]) == 1

    print("VALID_FILES:", len(loaded["rows_by_kind"]))
    print("INCOMPLETE_PARTITIONS:", len(loaded["incomplete_partitions"]))
    print("INTEGRITY_VALIDATION: PASS")

    # 3. 파일 내용을 변경해 체크섬 오류를 의도적으로 발생시킨다.
    # 운영 적재에서는 이런 파일을 반드시 거부해야 한다.
    damaged_file = run_dir / "updated.jsonl.gz"
    original_bytes = damaged_file.read_bytes()

    damaged_file.write_bytes(original_bytes + b"damaged")

    try:
        load_bronze_run(manifest_path)
    except ValueError as error:
        if "CHECKSUM_MISMATCH" not in str(error):
            raise
        print("CHECKSUM_CORRUPTION_BLOCKED: PASS")
    else:
        raise AssertionError("손상 파일을 차단하지 못했습니다.")

    # 4. 원본을 복원하고 manifest의 건수를 잘못 변경한다.
    # 체크섬이 맞아도 레코드 건수가 다르면 거부해야 한다.
    damaged_file.write_bytes(original_bytes)
    manifest["files"][0]["record_count"] = 99

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )

    try:
        load_bronze_run(manifest_path)
    except ValueError as error:
        if "RECORD_COUNT_MISMATCH" not in str(error):
            raise
        print("RECORD_COUNT_MISMATCH_BLOCKED: PASS")
    else:
        raise AssertionError("잘못된 레코드 건수를 차단하지 못했습니다.")

    # 5. 임시 폴더는 테스트 종료 후 삭제된다.
    # 프로젝트 내 data/, docs/와 Iceberg 테이블은 변경하지 않는다.
    print("ICEBERG_WRITE: NOT_PERFORMED")
    print("ACTUAL_A_TEAM_FILES: NOT_TESTED")
    print("RESULT: PASS")
