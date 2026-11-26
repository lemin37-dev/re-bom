"""Validate Bronze photo snapshots and plan Silver current-photo changes."""

import gzip
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime

from botocore.exceptions import ClientError

MANIFEST_PREFIX = "bronze/rescue/image_runs/"
SNAPSHOT_PREFIX = "bronze/rescue/image_current/"
SNAPSHOT_VERSION = date(2026, 11, 20)
PHOTO_FIELDS = ("original_s3_key", "serving_s3_key", "thumb_s3_key", "sha256")
CURRENT_FIELDS = (*PHOTO_FIELDS, "ext", "bytes", "source_url", "stored_at")
HASH = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)
RUN_PATH = re.compile(r"dt=(\d{4}-\d{2}-\d{2})/run_id=([A-Za-z0-9_-]+)/")


def _require(condition, reason):
    if not condition:
        raise ValueError(f"PHOTO_SNAPSHOT_{reason}")


def _timestamp(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def _date(value):
    _require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "VERSION")
    return date.fromisoformat(value)


def _read(s3, bucket, key):
    with closing(s3.get_object(Bucket=bucket, Key=key)["Body"]) as body:
        return body.read()


def slot_key(row):
    return row["desertion_no"], row["image_slot"]


def _positive(value):
    return type(value) is int and value > 0


def _parse_row(row):
    _require(isinstance(row, dict), "ROW_FORMAT")
    no, slot = row.get("desertion_no"), row.get("slot")
    _require(isinstance(no, str) and re.fullmatch(r"[0-9A-Za-z_-]{1,40}", no)
             and type(slot) is int and 1 <= slot <= 8, "SLOT_KEY")
    state = row.get("slot_state")
    _require(state in ("active", "emptied"), f"SLOT_STATE: {(no, slot)}")
    required = (*CURRENT_FIELDS, "current_version", "latest_version", "latest_status",
                "latest_url", "first_seen_at")
    _require(all(field in row for field in required), f"ROW_FIELDS: {(no, slot)}")
    cur, latest = row["current_version"], row["latest_version"]
    status = row["latest_status"]
    _require(_positive(latest) and status in ("success", "pending", "failed", "gave_up"),
             f"LATEST_VERSION: {(no, slot)}")
    if state == "emptied":
        _require(cur is None and row["latest_url"] is None, f"EMPTIED_FIELDS: {(no, slot)}")
    else:
        url = row["latest_url"]
        _require(isinstance(url, str) and bool(url.strip()), f"LATEST_URL: {(no, slot)}")
        if status == "success":
            _require(cur == latest, f"LATEST_SUCCESS: {(no, slot)}")
    for field in ("stored_at", "first_seen_at"):
        if row[field] is not None:
            timestamp = _timestamp(row[field])
            _require(timestamp is not None and timestamp.utcoffset() is not None,
                     f"ROW_TIMESTAMP: {slot}, {field}")
    if cur is None:
        _require(all(row[field] is None for field in CURRENT_FIELDS), f"NULL_FIELDS: {(no, slot)}")
    else:
        _require(_positive(cur) and cur <= latest and (cur != latest or status == "success"),
                 f"CURRENT_VERSION: {(no, slot)}")
        suffix = "" if cur == 1 else f"_v{cur:03d}"
        ext = row["ext"]
        _require(isinstance(ext, str) and re.fullmatch(r"[a-z0-9]+", ext), f"EXT: {(no, slot)}")
        _require(
            row["original_s3_key"] == f"bronze/rescue/images/{no}/popfile{slot}{suffix}.{ext}"
            and row["serving_s3_key"] == f"images/{no}/{slot}{suffix}.webp"
            and row["thumb_s3_key"] == f"images/{no}/{slot}{suffix}_thumb.webp",
            f"PATHS: {(no, slot)}",
        )
        _require(isinstance(row["sha256"], str) and HASH.fullmatch(row["sha256"])
                 and _positive(row["bytes"]) and isinstance(row["source_url"], str)
                 and bool(row["source_url"].strip()), f"STORAGE_FIELDS: {(no, slot)}")
        row["sha256"] = row["sha256"].lower()
    row["image_slot"] = row.pop("slot")
    return row


def _load(s3, bucket, key):
    meta = json.loads(_read(s3, bucket, key))
    match = RUN_PATH.fullmatch(key.removeprefix(SNAPSHOT_PREFIX).removesuffix("snapshot.json"))
    _require(isinstance(meta, dict) and match is not None
             and meta.get("photo_run_id") == match[2]
             and meta.get("source") == "rescue_photos" and meta.get("status") == "ok"
             and meta.get("file") == "current.jsonl.gz"
             and isinstance(meta.get("bronze_run_id"), str) and bool(meta["bronze_run_id"]),
             f"METADATA: {key}")
    generated = _timestamp(meta.get("generated_at"))
    _require(generated is not None and generated.utcoffset() is not None, f"GENERATED_AT: {key}")
    _require(_date(meta.get("code_version")) >= SNAPSHOT_VERSION, f"CODE_VERSION: {key}")

    data = _read(s3, bucket, key.removesuffix("snapshot.json") + "current.jsonl.gz")
    _require(hashlib.sha256(data).hexdigest() == meta.get("checksum"), f"CHECKSUM: {key}")
    rows = {}
    last_key = None
    counts = dict(stored_count=0, no_current_count=0, emptied_count=0)
    for line in gzip.decompress(data).decode("utf-8").splitlines():
        row = _parse_row(json.loads(line))
        slot = slot_key(row)
        _require(last_key is None or last_key < slot, f"SLOT_ORDER_OR_DUPLICATE: {slot}")
        last_key = slot
        rows[slot] = row
        if row["slot_state"] == "emptied":
            counts["emptied_count"] += 1
        elif row["current_version"] is None:
            counts["no_current_count"] += 1
        else:
            counts["stored_count"] += 1
    counts.update(row_count=len(rows), notice_count=len({no for no, _ in rows}))
    _require(all(type(meta.get(field)) is int and meta[field] == count
                 for field, count in counts.items()), f"COUNTS: {key}")
    reverted = meta.get("url_reverted_count")
    _require(type(reverted) is int and 0 <= reverted <= len(rows) - counts["emptied_count"],
             f"REVERTED_COUNT: {key}")
    return meta, rows


def _snapshot_keys(s3, bucket):
    paginator = s3.get_paginator("list_objects_v2")
    return sorted(
        item["Key"]
        for page in paginator.paginate(Bucket=bucket, Prefix=SNAPSHOT_PREFIX)
        for item in page.get("Contents", [])
        if item["Key"].endswith("/snapshot.json")
        and RUN_PATH.fullmatch(item["Key"].removeprefix(SNAPSHOT_PREFIX).removesuffix("snapshot.json"))
    )


def _bronze_slots(s3, bucket, run_id):
    _require(re.fullmatch(r"\d{8}T\d{6}-[0-9a-f]+", run_id), "BRONZE_RUN_ID")
    day = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"
    prefix = f"bronze/rescue/raw/dt={day}/run_id={run_id}/"
    try:
        metadata = json.loads(_read(s3, bucket, prefix + "manifest.json"))
        _require(metadata.get("status") == "ok" and metadata.get("run_id") == run_id,
                 "BRONZE_MANIFEST")
        notices = {}
        # Match the producer's first-row precedence across the two lists.
        for kind in ("list_notice", "list_protect"):
            name = f"{kind}.jsonl.gz"
            entries = [entry for entry in metadata.get("files", []) if entry.get("file") == name]
            _require(len(entries) == 1, f"BRONZE_FILE: {name}")
            data = _read(s3, bucket, prefix + name)
            _require(hashlib.sha256(data).hexdigest() == entries[0].get("checksum"),
                     f"BRONZE_CHECKSUM: {name}")
            for line in gzip.decompress(data).decode("utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                no = row.get("desertionNo")
                if no and re.fullmatch(r"[0-9A-Za-z_-]{1,40}", str(no)):
                    notices.setdefault(str(no), {
                        slot for slot in range(1, 9)
                        if isinstance(url := row.get(f"popfile{slot}"), str) and url.strip()
                    })
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
            raise ValueError(f"PHOTO_SNAPSHOT_BRONZE_MISSING: {run_id}") from exc
        raise
    return notices


def _verify_coverage(s3, bucket, manifest, rows, previous):
    attempts = [*manifest["files"], *manifest["failed"]]
    counts = manifest.get("counts")
    _require(isinstance(counts, dict), "WORK_COUNTS")
    attempted, planned = counts.get("this_run"), counts.get("to_process")
    _require(type(attempted) is int and type(planned) is int
             and attempted == len(attempts) <= planned, "WORK_COUNTS")
    notices = _bronze_slots(s3, bucket, manifest["bronze_run_id"])
    active = {(no, slot) for no, slots in notices.items() for slot in slots}

    required = set()
    for row in attempts:
        no, slot = str(row.get("desertionNo")), row.get("slot")
        _require(no in notices and type(slot) is int and 1 <= slot <= 8, "ATTEMPT_SLOT")
        required.add((no, slot))
    required.update(slot for slot in previous if slot[0] in notices)
    # Without truncation, every Bronze URL slot already had state or was attempted.
    if attempted == planned:
        required.update(active)
    missing = required - rows.keys()
    if missing:
        raise ValueError(f"PHOTO_SNAPSHOT_MISSING_REQUIRED_SLOT: {min(missing)} ({len(missing)} missing)")

    for slot, row in rows.items():
        _require(slot[0] in notices, f"NOTICE_OUTSIDE_BRONZE: {slot}")
        expected = "active" if slot in active else "emptied"
        _require(row["slot_state"] == expected, f"BRONZE_SLOT_STATE: {slot}")


def load_photo_snapshot(s3, bucket, manifest_key, manifest):
    """Select the snapshot protocol by producer version, with a dated legacy fallback."""
    if manifest["status"] == "aborted" and str(manifest.get("abort_reason", "")).startswith("snapshot:"):
        raise ValueError(f"PHOTO_SNAPSHOT_ABORTED: {manifest_key}")
    if manifest["status"] != "ok":
        return None
    partition = manifest_key.removeprefix(MANIFEST_PREFIX).removesuffix("manifest.json")
    match = RUN_PATH.fullmatch(partition)
    _require(match is not None, f"MANIFEST_PATH: {manifest_key}")
    code_version = manifest.get("code_version")
    # Unversioned manifests are legacy only in partitions before the rollout date.
    if _date(code_version if code_version is not None else match[1]) < SNAPSHOT_VERSION:
        return None

    key = SNAPSHOT_PREFIX + partition + "snapshot.json"
    try:
        meta, rows = _load(s3, bucket, key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
            raise ValueError(f"PHOTO_SNAPSHOT_MISSING: {key}") from exc
        raise
    _require(meta["photo_run_id"] == manifest["run_id"]
             and meta["bronze_run_id"] == manifest.get("bronze_run_id")
             and meta["code_version"] == code_version
             and meta["source"] == manifest.get("source"),
             f"MANIFEST_IDENTITY: {key}")
    started = _timestamp(manifest.get("generated_at"))
    _require(started is not None and started.utcoffset() is not None
             and started <= _timestamp(meta["generated_at"]), f"MANIFEST_TIME: {key}")

    previous = {}
    keys = _snapshot_keys(s3, bucket)
    previous_keys = [candidate for candidate in keys if candidate < key]
    if previous_keys:
        prior_meta, previous = _load(s3, bucket, previous_keys[-1])
        _require(_timestamp(prior_meta["generated_at"]) <= _timestamp(meta["generated_at"]),
                 f"SNAPSHOT_ORDER: {key}")
        for slot in rows.keys() & previous.keys():
            row, prior = rows[slot], previous[slot]
            _require(row["latest_version"] >= prior["latest_version"]
                     and (row["slot_state"] == "emptied"
                          or (row["current_version"] or 0) >= (prior["current_version"] or 0)),
                     f"VERSION_REGRESSION: {slot}")
            if row["current_version"] is not None and row["current_version"] == prior["current_version"]:
                _verify_identity(row, prior, slot)

    _verify_coverage(s3, bucket, manifest, rows, previous)

    def verify_objects(row):
        slot = slot_key(row)
        head = s3.head_object(Bucket=bucket, Key=row["original_s3_key"])
        _require(head.get("ContentLength") == row["bytes"]
                 and head.get("Metadata", {}).get("sha256", "").lower() == row["sha256"],
                 f"ORIGINAL_EVIDENCE: {slot}")
        for field in ("serving_s3_key", "thumb_s3_key"):
            head = s3.head_object(Bucket=bucket, Key=row[field])
            _require(_positive(head.get("ContentLength")), f"SERVING_EVIDENCE: {slot}, {field}")
    stored = [row for row in rows.values() if row["current_version"] is not None]
    if stored:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for start in range(0, len(stored), 256):
                for _ in pool.map(verify_objects, stored[start:start + 256]):
                    pass

    return {
        "checksum": meta["checksum"], "rows": rows,
        "later_runs": [
            candidate.split("/run_id=", 1)[1].split("/", 1)[0]
            for candidate in keys if candidate > key
        ],
    }


def _verify_identity(row, other, slot, *, compare_url=True):
    _require(all(row.get(field) == other.get(field) for field in PHOTO_FIELDS)
             and (not compare_url or not other.get("source_url") or row["source_url"] == other["source_url"]),
             f"VERSION_CONFLICT: {slot}")


def verify_snapshot_history(snapshot, history):
    """Match known successful versions and guard against stale snapshot replays."""
    for row in history:
        slot = slot_key(row)
        photo = snapshot["rows"].get(slot)
        if photo is None:
            continue
        _require(row["version"] <= photo["latest_version"], f"HISTORY_VERSION_REGRESSION: {slot}")
        if row["status"] != "stored":
            continue
        cur = photo["current_version"]
        _require(photo["slot_state"] == "emptied" or (cur or 0) >= row["version"],
                 f"HISTORY_CURRENT_REGRESSION: {slot}")
        if cur == row["version"]:
            _verify_identity(photo, row, slot)


def plan_photo_snapshot(current_rows, snapshot):
    current = {slot_key(row): row for row in current_rows}
    _require(len(current) == len(current_rows), "DUPLICATE_SILVER_SLOT")
    plan = {"updates": [], "inserts": [], "held_slots": []}
    for slot, row in snapshot["rows"].items():
        before = current.get(slot)
        if before is not None and before.get("original_s3_key"):
            stem = before["original_s3_key"].rsplit("/", 1)[-1].split(".", 1)[0]
            match = re.fullmatch(rf"popfile{slot[1]}(?:_v(\d{{3,}}))?", stem)
            if match is not None:
                version = int(match[1]) if match[1] else 1
                _require(row["latest_version"] >= version
                         and (row["slot_state"] == "emptied" or (row["current_version"] or 0) >= version),
                         f"SILVER_VERSION_REGRESSION: {slot}")
                if row["current_version"] == version:
                    _verify_identity(row, before, slot, compare_url=False)
        fields = {field: row[field] for field in (*PHOTO_FIELDS, "source_url")}
        if before is None:
            if row["current_version"] is not None:
                plan["inserts"].append(dict(desertion_no=slot[0], image_slot=slot[1], **fields))
        else:
            fields = {field: value for field, value in fields.items() if field != "source_url" or field in before}
            if any(before.get(field) != value for field, value in fields.items()):
                plan["updates"].append(dict(before, **fields))
    return plan


def verify_superseding_snapshot(s3, bucket, manifest_key, journal_prefix, journal, table):
    """Find the latest journaled snapshot that covers each legacy slot."""
    from rebom.storage.silver.photo_processing_journal import load_photo_journal_s3

    if not journal.get("processed_at") or table.current_snapshot() is None:
        return lambda slot, current: False

    key = manifest_key.replace(MANIFEST_PREFIX, SNAPSHOT_PREFIX, 1).removesuffix("manifest.json") + "snapshot.json"
    successor_keys = (
        candidate for candidate in reversed(_snapshot_keys(s3, bucket)) if candidate > key
    )
    covered = {}

    def matches(slot, current):
        while slot not in covered:
            candidate = next(successor_keys, None)
            if candidate is None:
                return False
            run_id = candidate.split("/run_id=", 1)[1].split("/", 1)[0]
            later = load_photo_journal_s3(s3, bucket, f"{journal_prefix.rstrip('/')}/{run_id}.json")
            if not later or not later.get("snapshot_checksum"):
                continue
            metadata, rows = _load(s3, bucket, candidate)
            _require(later["snapshot_checksum"] == metadata["checksum"], "JOURNAL_MISMATCH")
            for key_, row in rows.items():
                covered.setdefault(key_, row)
        row = covered[slot]
        return all(current.get(field) == row[field] for field in PHOTO_FIELDS)

    return matches
