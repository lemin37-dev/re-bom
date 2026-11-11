"""AWS Silver partial-write recovery, exercised with in-memory Iceberg stand-ins.

No AWS calls are made. The actual Glue/S3 deployment is not tested here.
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pytest

from rebom.storage.silver.run_journal import read_run


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "storage" / "rebom_run_aws_silver.py"
spec = importlib.util.spec_from_file_location("rebom_aws_silver_recovery_test_target", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class FakeSchema:
    def __init__(self, arrow):
        self.arrow = arrow

    def as_arrow(self):
        return self.arrow


class FakeTable:
    def __init__(self, name, schema, location):
        self._name = name
        self._schema = schema
        self._location = location
        self.rows = []
        self.writes = 0
        self.fail_once = False

    def location(self):
        return self._location

    def schema(self):
        return FakeSchema(self._schema)

    def name(self):
        return self._name

    def scan(self):
        table = pa.Table.from_pylist(self.rows, schema=self._schema)
        return SimpleNamespace(to_arrow=lambda: table)

    def upsert(self, arrow, join_cols, **kwargs):
        self.writes += 1
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("injected history write failure")
        indexed = {tuple(row[k] for k in join_cols): row for row in self.rows}
        inserts = 0
        updates = 0
        for row in arrow.to_pylist():
            key = tuple(row[k] for k in join_cols)
            if key in indexed:
                updates += 1
            else:
                inserts += 1
            indexed[key] = row
        self.rows = list(indexed.values())
        return SimpleNamespace(rows_inserted=inserts, rows_updated=updates)


class FakeCatalog:
    def __init__(self, notice, history):
        self.tables = {runner.NOTICE_NAME: notice, runner.HISTORY_NAME: history}

    def load_table(self, name):
        return self.tables[name]


def test_history_failure_recovers_without_rewriting_notice(tmp_path, monkeypatch):
    notice_schema = pa.schema([("desertion_no", pa.string()), ("status_group", pa.string())])
    history_schema = pa.schema([
        ("run_id", pa.string()), ("desertion_no", pa.string()),
        ("observed_on", pa.string()), ("event_type", pa.string()),
    ])
    notice = FakeTable("notice", notice_schema, runner.PREFIX + "silver_animal_notice_b_test")
    history = FakeTable("history", history_schema, runner.PREFIX + "silver_animal_status_history_b_test")
    catalog = FakeCatalog(notice, history)
    monkeypatch.setattr(runner, "get_tables", lambda: (catalog, notice, history))

    run_id = "20261111T120000-b00001"
    notice_arrow = pa.Table.from_pylist([{"desertion_no": "ABC001", "status_group": "PROTECTING"}], schema=notice_schema)
    history_arrow = pa.Table.from_pylist([{"run_id": run_id, "desertion_no": "ABC001", "observed_on": "2026-11-11", "event_type": "INITIAL"}], schema=history_schema)
    calls = []

    def fake_prepare(*args):
        calls.append(1)
        return {"ready": True, "run_id": run_id}, notice_arrow, history_arrow

    monkeypatch.setattr(runner, "prepare_new", fake_prepare)
    bronze = tmp_path / "bronze.json"
    events = tmp_path / "events.json"
    bronze.write_text('{"run_id":"' + run_id + '"}', encoding="utf-8")
    events.write_text("{}", encoding="utf-8")
    journal = tmp_path / "journal"
    args = SimpleNamespace(bronze_manifest=bronze, event_manifest=events, apply=True, journal_dir=journal)

    history.fail_once = True
    with pytest.raises(RuntimeError, match="injected history write failure"):
        runner.execute(args)

    assert notice.writes == 1
    assert history.writes == 1
    assert read_run(run_id, journal)["stage"] == "NOTICE_APPLIED"
    assert (journal / "prepared" / run_id / "notice.parquet").is_file()
    assert (journal / "prepared" / run_id / "history.parquet").is_file()

    monkeypatch.setattr(runner, "prepare_new", lambda *args: pytest.fail("must reuse persisted snapshot"))
    runner.execute(args)
    assert read_run(run_id, journal)["stage"] == "COMPLETED"
    assert notice.writes == 1
    assert history.writes == 2

    runner.execute(args)
    assert notice.writes == 1
    assert history.writes == 2
    assert len(calls) == 1


def test_reused_run_rejects_modified_manifest(tmp_path, monkeypatch):
    schema = pa.schema([("desertion_no", pa.string())])
    history_schema = pa.schema([
        ("run_id", pa.string()), ("desertion_no", pa.string()),
        ("observed_on", pa.string()), ("event_type", pa.string()),
    ])
    notice = FakeTable("notice", schema, runner.PREFIX + "silver_animal_notice_b_test")
    history = FakeTable("history", history_schema, runner.PREFIX + "silver_animal_status_history_b_test")
    catalog = FakeCatalog(notice, history)
    monkeypatch.setattr(runner, "get_tables", lambda: (catalog, notice, history))
    run_id = "20261111T130000-b00001"
    n = pa.Table.from_pylist([{"desertion_no": "ABC002"}], schema=schema)
    h = pa.Table.from_pylist([{"run_id": run_id, "desertion_no": "ABC002", "observed_on": "2026-11-11", "event_type": "INITIAL"}], schema=history_schema)
    monkeypatch.setattr(runner, "prepare_new", lambda *args: ({"ready": True, "run_id": run_id}, n, h))
    bronze = tmp_path / "bronze.json"
    events = tmp_path / "events.json"
    bronze.write_text('{"run_id":"' + run_id + '"}', encoding="utf-8")
    events.write_text("{}", encoding="utf-8")
    args = SimpleNamespace(bronze_manifest=bronze, event_manifest=events, apply=True, journal_dir=tmp_path / "journal")
    runner.execute(args)
    events.write_text('{"tampered":true}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="different manifest content"):
        runner.execute(args)
    assert notice.writes == 1
    assert history.writes == 1
