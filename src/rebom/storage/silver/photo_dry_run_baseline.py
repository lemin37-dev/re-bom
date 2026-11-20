"""Read verified v1 photos for the Silver DRY_RUN v2 comparison."""

import hashlib
import json
from contextlib import closing
from datetime import datetime

from rebom.storage.silver.load_photo_manifest import parse_photo_manifest
from rebom.storage.silver.photo_version_history import (
    build_photo_version_history_rows,
    select_current_photo_version,
)

PHOTO_PATHS_AND_HASH = (
    "original_s3_key", "serving_s3_key", "thumb_s3_key", "sha256",
)


def _slot(row):
    return row["desertion_no"], row["image_slot"]


def _identity(row, fields):
    return tuple(row.get(field) for field in fields)


def _timestamp(value):
    try:
        timestamp = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return timestamp if timestamp.utcoffset() is not None else None


def load_previous_photos(s3, bucket, manifest_keys, target_key, generated_at, slots):
    """Find finalized v1 records and hash only the requested original objects."""
    target_start = _timestamp(generated_at)
    candidates = {}
    if not slots or target_start is None:
        return candidates

    def wanted(item):
        return (str(item.get("desertionNo") or "").strip(), item.get("slot")) in slots

    for key in manifest_keys:
        if (
            key == target_key
            or not key.startswith("bronze/rescue/image_runs/")
            or not key.endswith("/manifest.json")
        ):
            continue
        with closing(s3.get_object(Bucket=bucket, Key=key)["Body"]) as body:
            source = json.loads(body.read())
        if source.get("status") not in {"ok", "aborted"}:
            continue
        finished = _timestamp(source.get("updated_at"))
        if finished is None or finished >= target_start:
            continue
        files = [
            item for item in source.get("files", [])
            if wanted(item) and item.get("version", 1) == 1
        ]
        if not files:
            continue
        if not key.endswith(f"/run_id={source.get('run_id')}/manifest.json"):
            raise ValueError(f"PHOTO_BASELINE_MANIFEST_KEY_MISMATCH: {key}")
        failed = [
            item for item in source.get("failed", [])
            if wanted(item) and item.get("version") == 1
        ]
        manifest = parse_photo_manifest(dict(source, files=files, failed=failed))
        for row in build_photo_version_history_rows(manifest):
            if row["status"] != "stored":
                continue
            slot = _slot(row)
            previous = candidates.setdefault(slot, row)
            fields = (*PHOTO_PATHS_AND_HASH, "replaces")
            if _identity(previous, fields) != _identity(row, fields):
                raise ValueError(f"PHOTO_BASELINE_MANIFEST_CONFLICT: {slot}")

    for slot, row in candidates.items():
        digest = hashlib.sha256()
        response = s3.get_object(Bucket=bucket, Key=row["original_s3_key"])
        with closing(response["Body"]) as body:
            for chunk in iter(lambda: body.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != row["sha256"]:
            raise RuntimeError(f"PHOTO_BASELINE_SHA256_MISMATCH: {slot}")

    return candidates


def photo_comparison_rows(current_rows, history_rows, load_baseline):
    """Augment only the comparison inputs for v2 replacements of v1."""
    current = {_slot(row): row for row in current_rows}
    history_v1 = {_slot(row): row for row in history_rows if row["version"] == 1}
    slots = {
        slot for slot, row in select_current_photo_version(history_rows).items()
        if row["version"] == 2 and row.get("replaces") == 1
        and (slot not in history_v1 or slot not in current)
        and (
            slot not in current
            or _identity(current[slot], PHOTO_PATHS_AND_HASH)
            != _identity(row, PHOTO_PATHS_AND_HASH)
        )
    }
    baseline = load_baseline(slots) if slots else {}
    virtual_current = []
    virtual_history = []
    for slot, row in baseline.items():
        existing = history_v1.get(slot)
        fields = (*PHOTO_PATHS_AND_HASH, "replaces", "status")
        if existing is None:
            virtual_history.append(row)
        elif _identity(existing, fields) != _identity(row, fields):
            raise ValueError(f"PHOTO_BASELINE_HISTORY_CONFLICT: {slot}")

        before = current.get(slot)
        if before is None:
            fields = ("desertion_no", "image_slot", "source_url", *PHOTO_PATHS_AND_HASH)
            virtual_current.append({field: row[field] for field in fields})
        elif (
            any(before.get(field) is not None for field in PHOTO_PATHS_AND_HASH)
            and (
                _identity(before, PHOTO_PATHS_AND_HASH) != _identity(row, PHOTO_PATHS_AND_HASH)
                or before.get("replaces") != row["replaces"]
                or before.get("version", 1) != 1
            )
        ):
            raise ValueError(f"PHOTO_BASELINE_CURRENT_CONFLICT: {slot}")

    return current_rows + virtual_current, history_rows + virtual_history
