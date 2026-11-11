import gzip
import hashlib
import json
import tempfile
from pathlib import Path

from rebom.storage.silver.load_silver_events import load_silver_events

run_id = "20261103T090045-1fe465"

with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp) / "dt=2026-11-03" / f"run_id={run_id}"
    folder.mkdir(parents=True)

    events = [
        {
            "event": "vanished",
            "desertionNo": "A001",
            "happenDt": "20261101",
            "confirmed_by": "lookup",
            "state_source": "lookup",
            "processState": "종료(입양)",
            "detected_at": "2026-11-03T09:00:45+09:00",
        }
    ]

    path = folder / "events.jsonl.gz"

    with gzip.open(path, "wt", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    manifest = {
        "status": "ok",
        "bronze_run_id": run_id,
        "generated_at": "2026-11-03T09:00:45+09:00",
        "files": [
            {
                "kind": "events",
                "file": "events.jsonl.gz",
                "record_count": 1,
                "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        ],
        "reprocess_ids": [],
        "lookup_deferred_ids": [],
        "conflicts": [],
    }

    manifest_path = folder / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = load_silver_events(manifest_path, run_id)

    assert len(result["rows_by_kind"]["events"]) == 1
    assert result["rows_by_kind"]["events"][0]["processState"] == "종료(입양)"
    print("SILVER_EVENTS_READ: PASS")

    try:
        load_silver_events(manifest_path, "20261103T090045-abcdef")
    except ValueError:
        print("RUN_ID_MISMATCH_BLOCKED: PASS")
    else:
        raise AssertionError("run_id 불일치가 허용됐습니다.")

    path.write_bytes(path.read_bytes() + b"damaged")

    try:
        load_silver_events(manifest_path, run_id)
    except ValueError as e:
        assert "CHECKSUM_MISMATCH" in str(e)
        print("CHECKSUM_MISMATCH_BLOCKED: PASS")
    else:
        raise AssertionError("손상된 파일이 허용됐습니다.")

print("RESULT: PASS")
