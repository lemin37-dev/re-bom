import gzip
import hashlib
import json
import tempfile
from pathlib import Path

from rebom.storage.silver.prepare_silver_pipeline import prepare_silver_pipeline
from rebom.storage.silver.validate_manifest import validate_partition

run_id = "20261103T090045-1fe465"
dt = "2026-11-03"
protecting = "\ubcf4\ud638\uc911"
closed = "\uc885\ub8cc(\uc785\uc591)"

def write_records(folder, records):
    folder.mkdir(parents=True, exist_ok=True)
    entries = []
    for kind, rows in records.items():
        path = folder / f"{kind}.jsonl.gz"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        entries.append({
            "kind": kind,
            "file": path.name,
            "record_count": len(rows),
            "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    return entries

def save_manifest(folder, data):
    path = folder / "manifest.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    bronze_dir = root / "bronze/rescue/raw" / f"dt={dt}" / f"run_id={run_id}"
    event_dir = root / "silver/rescue/events" / f"dt={dt}" / f"run_id={run_id}"

    bronze_files = write_records(bronze_dir, {
        "updated": [],
        "list_notice": [],
        "list_protect": [],
    })

    partitions = []
    for kind in ("list_notice", "list_protect"):
        complete, reason = validate_partition(
            "00", "2026-11-01", "2026-11-01", 0, 0
        )
        partitions.append({
            "kind": kind,
            "result_code": "00",
            "start_date": "2026-11-01",
            "end_date": "2026-11-01",
            "total_count": 0,
            "received_count": 0,
            "complete": complete,
            "reason": reason,
        })

    bronze_manifest = save_manifest(bronze_dir, {
        "run_id": run_id,
        "fetched_at": "2026-11-03T09:00:45+09:00",
        "status": "ok",
        "files": bronze_files,
        "partitions": partitions,
    })

    event_files = write_records(event_dir, {
        "events": [{
            "event": "vanished",
            "desertionNo": "A001",
            "happenDt": "20261101",
            "confirmed_by": "complete_range",
            "state_source": "relookup",
            "processState": closed,
            "detected_at": "2026-11-03T09:05:00+09:00",
        }],
        "lookup": [{
            "desertionNo": "A001",
            "happenDt": "20261101",
            "processState": closed,
        }],
    })

    event_manifest = save_manifest(event_dir, {
        "status": "ok",
        "bronze_run_id": run_id,
        "generated_at": "2026-11-03T09:05:00+09:00",
        "files": event_files,
        "reprocess_ids": [],
        "lookup_deferred_ids": [],
        "conflicts": [],
    })

    previous = [{
        "desertion_no": "A001",
        "happen_date": "2026-11-01",
        "process_state_raw": protecting,
        "status_group": "PROTECTING",
        "closure_reason_code": None,
        "source_updated_at_raw": None,
        "weight_min_kg": 5.2,
    }]

    result = prepare_silver_pipeline(
        bronze_manifest,
        event_manifest,
        previous,
        "2026-11-02",
    )

    assert result["ready"], result
    assert result["actual_closed_ids"] == ["A001"], result
    assert result["inferred_closed_ids"] == [], result
    assert len(result["notice_delta_rows"]) == 1, result
    assert len(result["history_rows"]) == 1, result

    notice = result["notice_delta_rows"][0]
    history = result["history_rows"][0]

    assert notice["process_state_raw"] == closed
    assert history["process_state_raw"] == closed
    assert notice["closure_reason_code"] == "\uc785\uc591"
    assert notice["weight_min_kg"] == 5.2
    assert history["is_inferred"] is False
    assert result["iceberg_write"] is False

    print("BRONZE_TO_SILVER_EVENTS: PASS")
    print("NOTICE_HISTORY_CONSISTENCY: PASS")
    print("EXISTING_FIELDS_PRESERVED: PASS")
    print("ICEBERG_WRITE: NOT_PERFORMED")
    print("RESULT: PASS")
