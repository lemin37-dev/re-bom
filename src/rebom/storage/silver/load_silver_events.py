"""A팀 Silver 이벤트 파일을 검증하고 B팀 처리 입력으로 변환한다."""

import gzip
import hashlib
import json
import re
from pathlib import Path

from rebom.storage.silver.load_bronze import read_jsonl_gz


def load_silver_events(manifest_path, expected_run_id):
    manifest_path = Path(manifest_path)
    base_dir = manifest_path.parent.resolve()

    with manifest_path.open(encoding="utf-8-sig") as f:
        manifest = json.load(f)

    if manifest.get("status") != "ok":
        raise ValueError("Silver 이벤트 실행 상태 오류")

    if manifest.get("bronze_run_id") != expected_run_id:
        raise ValueError("Bronze/Silver run_id 불일치")

    if base_dir.name != f"run_id={expected_run_id}":
        raise ValueError("Silver 이벤트 경로 run_id 불일치")

    files = manifest.get("files")

    if not isinstance(files, list):
        raise ValueError("Silver 이벤트 파일 목록 오류")

    rows_by_kind = {}

    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Silver 이벤트 파일 정보 오류")

        kind = item.get("kind")

        if kind not in {"events", "lookup"} or kind in rows_by_kind:
            raise ValueError(f"Silver 이벤트 파일 종류 오류: {kind}")

        filename = item.get("file")

        if filename != f"{kind}.jsonl.gz":
            raise ValueError("Silver 이벤트 파일명 오류")

        path = (base_dir / filename).resolve()

        if path.parent != base_dir or not path.is_file():
            raise ValueError(f"Silver 이벤트 파일 누락: {filename}")

        actual_checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        expected_checksum = item.get("checksum")

        if (
            not isinstance(expected_checksum, str)
            or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_checksum)
            or actual_checksum.lower() != expected_checksum.lower()
        ):
            raise ValueError(f"{kind}: CHECKSUM_MISMATCH")

        rows = read_jsonl_gz(path)

        expected_count = item.get("record_count")

        if type(expected_count) is not int or len(rows) != expected_count:
            raise ValueError(f"{kind}: RECORD_COUNT_MISMATCH")

        rows_by_kind[kind] = rows

    if "events" not in rows_by_kind:
        raise ValueError("필수 Silver events 파일 누락")

    for field in ("reprocess_ids", "lookup_deferred_ids", "conflicts"):
        if not isinstance(manifest.get(field), list):
            raise ValueError(f"Silver 이벤트 manifest 필드 오류: {field}")

    for event in rows_by_kind["events"]:
        if event.get("event") not in {"vanished", "returned"}:
            raise ValueError("알 수 없는 Silver 이벤트 종류")

        if not str(event.get("desertionNo") or "").strip():
            raise ValueError("Silver 이벤트 공고번호 누락")

    return {
        "bronze_run_id": expected_run_id,
        "rows_by_kind": rows_by_kind,
        "reprocess_ids": manifest["reprocess_ids"],
        "lookup_deferred_ids": manifest["lookup_deferred_ids"],
        "conflicts": manifest["conflicts"],
        "generated_at": manifest.get("generated_at"),
    }
