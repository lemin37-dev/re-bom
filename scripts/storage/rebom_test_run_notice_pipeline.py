"""Bronze → Silver 공통 실행 함수의 정상·충돌·손상 검증."""

import gzip
import hashlib
import json
import tempfile
from copy import deepcopy
from pathlib import Path

from rebom.storage.silver.run_notice_pipeline import run_notice_pipeline
from rebom.storage.silver.transform_notice import transform_notice


# 기존 Silver 공고 두 건을 준비한다.
# A는 이후 내용이 바뀌고, B는 변경되지 않는다.
previous = [
    {
        "desertionNo": "A",
        "processState": "보호중",
        "specialMark": "사람을 좋아함",
    },
    {
        "desertionNo": "B",
        "processState": "보호중",
        "specialMark": "활발함",
    },
]

existing_silver = [transform_notice(row) for row in previous]

changed_a = deepcopy(previous[0])
changed_a["specialMark"] = "사람과 다른 동물을 좋아함"

new_c = {
    "desertionNo": "C",
    "processState": "보호중",
    "specialMark": "온순함",
}

run_id = "20261102T160000-abcdef"
fetched_at = "2026-11-02T16:00:00+09:00"


def write_run(run_dir, updated, notice, protect):
    # A팀 계약 형식으로 4종의 압축 파일을 생성한다.
    # 파일을 쓸 때마다 SHA-256을 새로 계산해 manifest에 넣는다.
    kinds = {
        "updated": updated,
        "list_notice": notice,
        "list_protect": protect,
        "events": [],
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
                "start_date": "2026-11-01",
                "end_date": "2026-11-02",
                "total_count": len(updated),
                "received_count": len(updated),
                "complete": True,
                "reason": "complete",
                "reprocess_ids": [],
            }
        ],
        "reprocess_ids": [],
    }

    path = run_dir / "manifest.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )

    return path


with tempfile.TemporaryDirectory(prefix="rebom_pipeline_") as temp:
    run_dir = Path(temp) / "dt=2026-11-02" / f"run_id={run_id}"
    run_dir.mkdir(parents=True)

    # 1. 정상 데이터 검증
    # A는 UPDATE, B는 변경 없음, C는 INSERT 대상이다.
    manifest_path = write_run(
        run_dir,
        updated=[changed_a, new_c],
        notice=[deepcopy(changed_a)],
        protect=[deepcopy(previous[1])],
    )

    result = run_notice_pipeline(manifest_path, existing_silver)

    assert result["ready"] is True
    assert result["insert_count"] == 1
    assert result["update_count"] == 1
    assert result["unchanged_count"] == 1
    assert len(result["delta_rows"]) == 2
    assert result["statistics"]["duplicate_count"] == 1

    print("NORMAL_PIPELINE: PASS")
    print("PREPARED_INSERT:", result["insert_count"])
    print("PREPARED_UPDATE:", result["update_count"])
    print("UNCHANGED:", result["unchanged_count"])

    # 2. 출처 간 상태 충돌 검증
    # updated의 A는 보호중, list_notice의 A는 종료 상태다.
    # 어느 쪽을 선택할지 정해지지 않았으므로 쓰기를 금지한다.
    conflicting_a = deepcopy(changed_a)
    conflicting_a["processState"] = "종료(입양)"

    manifest_path = write_run(
        run_dir,
        updated=[changed_a, new_c],
        notice=[conflicting_a],
        protect=[deepcopy(previous[1])],
    )

    result = run_notice_pipeline(manifest_path, existing_silver)

    assert result["ready"] is False
    assert result["delta_rows"] == []
    assert result["conflicts"]
    assert result["conflicts"][0]["desertion_no"] == "A"

    print("CONFLICT_BLOCKED: PASS")

    # 3. 파일 손상 검증
    # manifest는 그대로 두고 압축 파일 바이트만 변경한다.
    # 체크섬 불일치라면 정제 함수를 호출하기 전에 중단되어야 한다.
    damaged_file = run_dir / "updated.jsonl.gz"

    with damaged_file.open("ab") as f:
        f.write(b"corrupted")

    try:
        run_notice_pipeline(manifest_path, existing_silver)
    except ValueError as error:
        assert "CHECKSUM_MISMATCH" in str(error)
        print("CHECKSUM_BLOCKED: PASS")
    else:
        raise AssertionError("파일 손상을 차단하지 못했습니다.")

print("ICEBERG_WRITE: NOT_PERFORMED")
print("ACTUAL_A_TEAM_FILES: NOT_TESTED")
print("RESULT: PASS")
