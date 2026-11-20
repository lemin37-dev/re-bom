import gzip
import hashlib
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pyarrow as pa
import pytest
from botocore.exceptions import ClientError

from rebom.storage.silver.photo_pipeline import process_photo_manifest
from rebom.storage.silver.photo_snapshot import CURRENT_FIELDS, PHOTO_FIELDS


def photo(no, version=1, *, emptied=False, pending=False):
    suffix = "" if version == 1 else f"_v{version:03d}"
    row = {
        "desertion_no": no, "slot": 1, "slot_state": "active",
        "current_version": version, "latest_version": version, "latest_status": "success",
        "original_s3_key": f"bronze/rescue/images/{no}/popfile1{suffix}.jpg",
        "serving_s3_key": f"images/{no}/1{suffix}.webp",
        "thumb_s3_key": f"images/{no}/1{suffix}_thumb.webp",
        "sha256": hashlib.sha256(f"{no}-{version}".encode()).hexdigest(),
        "bytes": len(f"{no}-{version}".encode()), "ext": "jpg",
        "source_url": f"https://example.test/{no}/{version}",
        "stored_at": "2026-11-19T10:00:00+09:00",
        "latest_url": f"https://example.test/{no}/{version}",
        "first_seen_at": "2026-11-19T10:00:00+09:00",
    }
    if emptied or pending:
        row.update(dict.fromkeys(CURRENT_FIELDS))
        row["current_version"] = None
    if emptied:
        row.update(slot_state="emptied", latest_url=None)
    if pending:
        row["latest_status"] = "pending"
    return row


def manifest_file(row):
    version = row["current_version"]
    return {
        "desertionNo": row["desertion_no"], "slot": row["slot"],
        "sha256": row["sha256"], "ext": row["ext"], "bytes": row["bytes"],
        "original": row["original_s3_key"],
        "serving": {"image": row["serving_s3_key"], "thumb": row["thumb_s3_key"]},
        "version": version, "replaces": version - 1 if version > 1 else None,
        "first_seen_at": row["first_seen_at"], "source_url": row["source_url"],
    }


def silver_photo(row):
    return {"desertion_no": row["desertion_no"], "image_slot": row["slot"],
            **{field: row[field] for field in (*PHOTO_FIELDS, "source_url")}}


def silver_history(row, run="previous"):
    version = row["current_version"]
    return dict(silver_photo(row), version=version, replaces=version - 1 if version > 1 else None,
                first_seen_at=row["first_seen_at"], status="stored", failure_reason=None, run_id=run)


class MemoryTable:
    def __init__(self, name, fields, keys):
        self.name = name
        self.keys = keys
        self.fields = pa.schema([
            (field, pa.int32() if field in {"image_slot", "version", "replaces"} else pa.string())
            for field in fields
        ])
        self.rows = {}
        self.snapshots = []
        self.overwrite = Mock(side_effect=lambda arrow, **kwargs: self.write(arrow))
        self.append = Mock(side_effect=self.write)
        self.upsert = Mock(side_effect=lambda arrow, **kwargs: self.write(arrow))

    def location(self):
        return f"s3://test/{self.name}"

    def schema(self):
        return SimpleNamespace(as_arrow=lambda: self.fields)

    def seed(self, *rows):
        for row in rows:
            self.rows[tuple(row[key] for key in self.keys)] = row
        self.snapshots.append(list(self.rows.values()))

    def write(self, arrow):
        previous = len(self.rows)
        self.seed(*arrow.to_pylist())
        inserted = len(self.rows) - previous
        return SimpleNamespace(rows_inserted=inserted, rows_updated=len(arrow) - inserted)

    def scan(self, *, snapshot_id=None):
        rows = self.snapshots[snapshot_id - 1] if snapshot_id is not None else list(self.rows.values())
        return SimpleNamespace(to_arrow=lambda: pa.Table.from_pylist(rows, schema=self.fields))

    def current_snapshot(self):
        return SimpleNamespace(snapshot_id=len(self.snapshots)) if self.snapshots else None


class MemoryRun:
    def __init__(self):
        example = photo("A")
        self.current = MemoryTable("current", silver_photo(example), ("desertion_no", "image_slot"))
        self.history = MemoryTable("history", silver_history(example), ("desertion_no", "image_slot", "version"))
        self.catalog = SimpleNamespace(load_table={"current": self.current, "history": self.history}.__getitem__)
        self.objects = {}
        self.heads = {}
        self.s3 = Mock()
        self.s3.get_object.side_effect = self.get_object
        self.s3.head_object.side_effect = lambda Bucket, Key: self.heads[Key]
        self.s3.put_object.side_effect = lambda Bucket, Key, Body, **kwargs: self.objects.__setitem__(Key, Body)
        self.s3.get_paginator.return_value.paginate.side_effect = self.list_objects

    def list_objects(self, *, Bucket, Prefix):
        return [{"Contents": [{"Key": key} for key in self.objects if key.startswith(Prefix)]}]

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": BytesIO(self.objects[Key])}

    def store_manifest(self, day, run_id, *, files=(), **overrides):
        key = f"bronze/rescue/image_runs/dt={day}/run_id={run_id}/manifest.json"
        bronze_run_id = f"{day.replace('-', '')}T090000-{hashlib.sha256(run_id.encode()).hexdigest()[:6]}"
        failed = overrides.get("failed", [])
        attempted = len(files) + len(failed)
        payload = dict(
            source="rescue_photos", status="ok", run_id=run_id, bronze_run_id=bronze_run_id,
            counts={"this_run": attempted, "to_process": attempted},
            generated_at=f"{day}T10:00:00+09:00", updated_at=f"{day}T10:01:00+09:00",
            code_version="2026-11-20", files=[manifest_file(row) for row in files], failed=[],
        )
        self.set_json(key, dict(payload, **overrides))
        return key

    def set_json(self, key, value):
        self.objects[key] = json.dumps(value).encode()

    def store_bronze(self, run_id, notices, protected=()):
        day = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"
        prefix = f"bronze/rescue/raw/dt={day}/run_id={run_id}/"
        files = []
        for kind, rows in (("list_notice", notices), ("list_protect", protected)):
            name = f"{kind}.jsonl.gz"
            data = gzip.compress("".join(json.dumps(row) + "\n" for row in rows).encode(), mtime=0)
            self.objects[prefix + name] = data
            files.append({"kind": kind, "file": name, "checksum": hashlib.sha256(data).hexdigest()})
        self.set_json(prefix + "manifest.json", {"run_id": run_id, "status": "ok", "files": files})
        return prefix

    def store_snapshot(self, key, rows, *, bronze_rows=None, **overrides):
        manifest = json.loads(self.objects[key])
        if bronze_rows is None:
            notices = {}
            for row in rows:
                no = row["desertion_no"]
                notice = notices.setdefault(no, {"desertionNo": no})
                if row["slot_state"] == "active":
                    notice[f"popfile{row['slot']}"] = row["latest_url"]
            bronze_rows = list(notices.values())
        self.store_bronze(manifest["bronze_run_id"], bronze_rows)
        prefix = key.replace("image_runs/", "image_current/").removesuffix("manifest.json")
        data = gzip.compress("".join(json.dumps(row) + "\n" for row in rows).encode(), mtime=0)
        meta = dict(
            source="rescue_photos", status="ok", photo_run_id=manifest["run_id"],
            bronze_run_id=manifest["bronze_run_id"], generated_at=manifest["updated_at"],
            code_version="2026-11-20", file="current.jsonl.gz",
            checksum=hashlib.sha256(data).hexdigest(), row_count=len(rows),
            notice_count=len({row["desertion_no"] for row in rows}),
            stored_count=sum(row["current_version"] is not None for row in rows),
            no_current_count=sum(row["slot_state"] == "active" and row["current_version"] is None for row in rows),
            emptied_count=sum(row["slot_state"] == "emptied" for row in rows),
            url_reverted_count=0,
        )
        self.objects[prefix + "current.jsonl.gz"] = data
        self.set_json(prefix + "snapshot.json", dict(meta, **overrides))
        for row in rows:
            if row["current_version"] is None:
                continue
            self.heads[row["original_s3_key"]] = {
                "ContentLength": row["bytes"], "Metadata": {"sha256": row["sha256"]},
            }
            for field in ("serving_s3_key", "thumb_s3_key"):
                self.heads[row[field]] = {"ContentLength": 100}

    def execute(self, key, **kwargs):
        return process_photo_manifest(
            self.s3, self.catalog, "test", key, "current", self.current.location(), "journal",
            history_table_name="history", expected_history_location=self.history.location(), **kwargs,
        )

    def assert_no_writes(self):
        for table in (self.current, self.history):
            table.append.assert_not_called()
            table.overwrite.assert_not_called()
            table.upsert.assert_not_called()
        self.s3.put_object.assert_not_called()


@pytest.fixture
def run():
    return MemoryRun()


def test_snapshot_drives_current_diff_with_empty_manifest(run):
    run.current.seed(*(silver_photo(photo(no)) for no in ("CHANGE", "CLEAR", "OMITTED", "SAME")))
    run.history.seed(*(silver_history(photo(no)) for no in ("CHANGE", "CLEAR", "OMITTED", "SAME")))
    key = run.store_manifest("2026-11-20", "run")
    run.store_snapshot(key, [
        photo("CHANGE", 2), photo("CLEAR", emptied=True), photo("NEW"),
        photo("PENDING", pending=True), photo("SAME"),
    ])
    result = run.execute(key)
    assert result["result"] == "DRY_RUN_PASS"
    assert result["changed_rows"] == 3
    assert result["history_insert_count"] == 0
    assert not result["held_slots"]
    run.assert_no_writes()


@pytest.mark.parametrize("problem, expected", [
    ("missing_metadata", "MISSING"),
    ("missing_data", "MISSING"),
    ("aborted", "ABORTED"),
    ("checksum", "CHECKSUM"),
    ("counts", "COUNTS"),
    ("run_id", "METADATA"),
    ("bronze_id", "MANIFEST_IDENTITY"),
    ("duplicate", "SLOT_ORDER_OR_DUPLICATE"),
    ("unsorted", "SLOT_ORDER_OR_DUPLICATE"),
    ("slot", "SLOT_KEY"),
    ("nulls", "NULL_FIELDS"),
    ("hash", "ORIGINAL_EVIDENCE"),
    ("serving", "SERVING_EVIDENCE"),
    ("path", "PATHS"),
    ("version", "CURRENT_VERSION"),
])
def test_invalid_snapshot_blocks_before_any_write(run, problem, expected):
    if problem == "aborted":
        key = run.store_manifest("2026-11-20", "run", status="aborted", abort_reason="snapshot: failed")
    else:
        key = run.store_manifest("2026-11-20", "run")
    row = photo("A", pending=problem == "nulls")
    rows = [row]
    metadata = {}
    if problem == "duplicate":
        rows.append(dict(row))
    elif problem == "unsorted":
        rows.insert(0, photo("B"))
    elif problem == "slot":
        row["slot"] = 9
    elif problem == "nulls":
        row["sha256"] = "a" * 64
    elif problem == "path":
        row["original_s3_key"] = "invalid"
    elif problem == "version":
        row.update(current_version=2, latest_status="failed")
    elif problem == "checksum":
        metadata["checksum"] = "b" * 64
    elif problem == "counts":
        metadata["stored_count"] = 0
    elif problem == "run_id":
        metadata["photo_run_id"] = "wrong"
    elif problem == "bronze_id":
        metadata["bronze_run_id"] = "wrong"
    if problem not in ("missing_metadata", "aborted"):
        run.store_snapshot(key, rows, **metadata)
    if problem == "missing_data":
        del run.objects[key.replace("image_runs/", "image_current/").replace("manifest.json", "current.jsonl.gz")]
    if problem == "hash":
        run.heads[row["original_s3_key"]]["Metadata"]["sha256"] = "c" * 64
    if problem == "serving":
        run.heads[row["serving_s3_key"]]["ContentLength"] = 0

    with pytest.raises(ValueError, match=f"PHOTO_SNAPSHOT_{expected}"):
        run.execute(key)
    run.assert_no_writes()


@pytest.mark.parametrize("legacy_version", [None, "2026-11-19"])
def test_legacy_manifest_does_not_read_snapshot(run, legacy_version):
    key = run.store_manifest("2026-11-19", "legacy", code_version=legacy_version)
    result = run.execute(key)
    assert result["result"] == "DRY_RUN_PASS"
    run.s3.get_paginator.assert_not_called()
    run.assert_no_writes()


def test_snapshot_version_cannot_regress_from_preceding_snapshot(run):
    previous = run.store_manifest("2026-11-20", "first")
    # An emptied slot still carries its latest observed version.
    run.store_snapshot(previous, [photo("A", 2, emptied=True)])
    key = run.store_manifest("2026-11-21", "second")
    run.store_snapshot(key, [photo("A")])
    with pytest.raises(ValueError, match="PHOTO_SNAPSHOT_VERSION_REGRESSION"):
        run.execute(key)
    run.assert_no_writes()


def test_snapshot_does_not_override_silver_version_identity(run):
    before = photo("A")
    run.history.seed(silver_history(before))
    key = run.store_manifest("2026-11-20", "run")
    run.store_snapshot(key, [dict(before, sha256="b" * 64)])
    with pytest.raises(ValueError, match="PHOTO_SNAPSHOT_VERSION_CONFLICT"):
        run.execute(key)
    run.assert_no_writes()


@pytest.mark.parametrize("in_manifest", [True, False])
def test_snapshot_v2_inserts_without_silver_v1_in_dry_run_and_apply(run, in_manifest):
    v2 = photo("A", 2)
    key = run.store_manifest("2026-11-20", "run", files=[v2] if in_manifest else [])
    run.store_snapshot(key, [v2])

    dry = run.execute(key)
    assert dry["result"] == "DRY_RUN_PASS"
    assert dry["held_slots"] == []
    assert dry["changed_rows"] == 1
    assert dry["history_insert_count"] == int(in_manifest)
    assert run.history.rows == run.current.rows == {}
    run.assert_no_writes()

    # Snapshot planning must not read a DRY_RUN-only historical baseline.
    with_baseline_keys = run.execute(key, historical_manifest_keys=[
        "bronze/rescue/image_runs/dt=2026-11-19/run_id=unavailable/manifest.json",
    ])
    assert with_baseline_keys == dry
    applied = run.execute(key, apply=True)
    assert applied["result"] == "APPLIED_AND_VERIFIED"
    assert applied["held_slots"] == dry["held_slots"]
    assert applied["current_changed"] == dry["changed_rows"]
    assert applied["history_inserted"] == dry["history_insert_count"]
    assert run.current.rows == {("A", 1): silver_photo(v2)}
    assert set(run.history.rows) == ({("A", 1, 2)} if in_manifest else set())
    run.current.append.assert_called_once()
    run.current.overwrite.assert_not_called()
    assert run.execute(key)["result"] == "ALREADY_PROCESSED"


def test_mock_apply_recovers_then_journal_replays_the_committed_snapshot(run):
    original = photo("A")
    run.current.seed(silver_photo(original), silver_photo(photo("OMITTED")))
    run.history.seed(silver_history(original))
    added = photo("B")
    key = run.store_manifest("2026-11-20", "first", files=[added])
    run.store_snapshot(key, [photo("A", emptied=True), added])
    run.current.overwrite.side_effect = RuntimeError("CURRENT_WRITE_INTERRUPTED")

    with pytest.raises(RuntimeError, match="CURRENT_WRITE_INTERRUPTED"):
        run.execute(key, apply=True)
    assert ("B", 1, 1) in run.history.rows
    run.s3.put_object.assert_not_called()

    run.current.overwrite.side_effect = lambda arrow, **kwargs: run.current.write(arrow)
    result = run.execute(key, apply=True)
    assert result["history_inserted"] == 0
    assert result["current_changed"] == 2
    assert run.current.rows[("A", 1)] == silver_photo(photo("A", emptied=True))
    assert ("A", 1, 1) in run.history.rows
    assert run.current.rows[("OMITTED", 1)] == silver_photo(photo("OMITTED"))

    later = run.store_manifest("2026-11-21", "second")
    run.store_snapshot(later, [original, photo("B", emptied=True)])
    assert run.execute(later, apply=True)["current_changed"] == 2
    assert run.execute(key)["result"] == "ALREADY_PROCESSED"
    assert run.current.rows[("A", 1)] == silver_photo(original)
    assert run.s3.put_object.call_count == 2


def test_later_snapshot_preserves_legacy_journal_replay_when_slot_is_omitted(run):
    original = photo("A")
    legacy = run.store_manifest("2026-11-19", "legacy", code_version="2026-11-19", files=[original])
    run.heads[original["original_s3_key"]] = {"Metadata": {"sha256": original["sha256"]}}
    assert run.execute(legacy, apply=True)["result"] == "APPLIED_AND_VERIFIED"

    first = run.store_manifest("2026-11-20", "empty")
    run.store_snapshot(first, [photo("A", emptied=True)])
    assert run.execute(first, apply=True)["current_changed"] == 1

    latest = run.store_manifest("2026-11-21", "unrelated")
    run.store_snapshot(latest, [photo("B")])
    assert run.execute(latest, apply=True)["current_changed"] == 1

    assert run.execute(legacy)["result"] == "ALREADY_PROCESSED"
    assert run.current.rows[("A", 1)] == silver_photo(photo("A", emptied=True))
    assert run.s3.put_object.call_count == 3


def test_unjournaled_older_snapshot_cannot_restore_a_cleared_slot(run):
    first = run.store_manifest("2026-11-20", "old")
    run.store_snapshot(first, [photo("A")])
    latest = run.store_manifest("2026-11-21", "latest")
    run.store_snapshot(latest, [photo("A", emptied=True)])
    assert run.execute(latest, apply=True)["result"] == "APPLIED_AND_VERIFIED"

    with pytest.raises(ValueError, match="PHOTO_SNAPSHOT_SUPERSEDED"):
        run.execute(first)
    assert run.current.rows == {}
    assert run.s3.put_object.call_count == 1


def test_snapshot_refreshes_silver_source_url_with_same_stored_photo(run):
    row = photo("A")
    run.current.seed(dict(silver_photo(row), source_url="https://example.test/stale"))
    key = run.store_manifest("2026-11-20", "new")
    run.store_snapshot(key, [row])

    assert run.execute(key)["changed_rows"] == 1
    run.assert_no_writes()


def test_manifest_storage_conflict_cannot_be_hidden_by_snapshot(run):
    row = photo("A")
    key = run.store_manifest("2026-11-20", "new", files=[dict(row, sha256="b" * 64)])
    run.store_snapshot(key, [row])

    with pytest.raises(ValueError, match="PHOTO_SNAPSHOT_VERSION_CONFLICT"):
        run.execute(key)
    run.assert_no_writes()


@pytest.mark.parametrize("missing_slot", [("B", 1), ("A", 2)])
def test_internally_consistent_snapshot_missing_bronze_photo_slot(run, missing_slot):
    key = run.store_manifest("2026-11-20", "run")
    rows = [photo("A"), photo("B")]
    if missing_slot == ("B", 1):
        rows.pop()
    run.store_snapshot(key, rows, bronze_rows=[
        {"desertionNo": "A", "popfile1": "https://example.test/a",
         **({"popfile2": "https://example.test/a2"} if missing_slot == ("A", 2) else {})},
        {"desertionNo": "B", "popfile1": "https://example.test/b"},
    ])

    with pytest.raises(ValueError, match="PHOTO_SNAPSHOT_MISSING_REQUIRED_SLOT"):
        run.execute(key)
    run.assert_no_writes()


def test_limited_snapshot_may_omit_unattempted_slots_and_notices_without_photos(run):
    stored = photo("A")
    key = run.store_manifest("2026-11-20", "run", files=[stored],
                             counts={"this_run": 1, "to_process": 2})
    run.store_snapshot(key, [stored], bronze_rows=[
        {"desertionNo": "A", "popfile1": stored["latest_url"]},
        {"desertionNo": "B", "popfile1": "https://example.test/unattempted"},
        {"desertionNo": "NO_PHOTOS"},
    ])

    assert run.execute(key)["result"] == "DRY_RUN_PASS"
    run.assert_no_writes()


def test_limited_snapshot_requires_failed_attempt_even_without_silver_history_row(run):
    key = run.store_manifest("2026-11-20", "run", counts={"this_run": 1, "to_process": 2},
                             failed=[{"desertionNo": "A", "slot": 1, "reason": "timeout"}])
    run.store_snapshot(key, [], bronze_rows=[{"desertionNo": "A", "popfile1": "https://example.test/a"}])

    with pytest.raises(ValueError, match="PHOTO_SNAPSHOT_MISSING_REQUIRED_SLOT"):
        run.execute(key)
    run.assert_no_writes()


def test_limited_snapshot_requires_prior_slot_for_retained_notice_even_without_url(run):
    previous = run.store_manifest("2026-11-20", "old")
    run.store_snapshot(previous, [photo("A"), photo("DEPARTED")])
    key = run.store_manifest("2026-11-21", "new", counts={"this_run": 0, "to_process": 1})
    bronze = [{"desertionNo": "A"}]
    run.store_snapshot(key, [], bronze_rows=bronze)

    with pytest.raises(ValueError, match="PHOTO_SNAPSHOT_MISSING_REQUIRED_SLOT"):
        run.execute(key)

    run.store_snapshot(key, [photo("A", emptied=True)], bronze_rows=bronze)
    assert run.execute(key)["result"] == "DRY_RUN_PASS"
    run.assert_no_writes()


def test_bronze_protect_list_and_checksum_are_used_for_coverage(run):
    key = run.store_manifest("2026-11-20", "run")
    run.store_snapshot(key, [])
    bronze_run_id = json.loads(run.objects[key])["bronze_run_id"]
    prefix = run.store_bronze(bronze_run_id, [], [{"desertionNo": "P", "popfile1": "https://example.test/p"}])

    with pytest.raises(ValueError, match="PHOTO_SNAPSHOT_MISSING_REQUIRED_SLOT"):
        run.execute(key)

    run.objects[prefix + "list_protect.jsonl.gz"] = gzip.compress(b"")
    with pytest.raises(ValueError, match="PHOTO_SNAPSHOT_BRONZE_CHECKSUM"):
        run.execute(key)
    run.assert_no_writes()
