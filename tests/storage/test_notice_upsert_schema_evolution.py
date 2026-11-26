import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pyarrow as pa
import pytest
from botocore.exceptions import ClientError
from pyiceberg.catalog import load_catalog
from pyiceberg.expressions import EqualTo
from pyiceberg.types import StringType

from rebom.storage.silver.apply_silver_pipeline import upsert_notice
from rebom.storage.silver.s3_run_journal import current_stage, stage_key
from rebom.storage.silver.s3_run_snapshot import SNAPSHOT_FILES
from scripts.storage import rebom_run_aws_real_silver_test as runner


FIELDS = ("sfe_soci", "sfe_health", "health_chk", "vaccination_chk")


def arrow(table, *rows):
    return pa.Table.from_pylist(rows, schema=table.schema().as_arrow())


@pytest.fixture
def evolved_notice(tmp_path):
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    catalog = load_catalog(
        "isolated_notice_upsert", type="sql",
        uri=f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}",
        warehouse=str(warehouse),
    )
    catalog.create_namespace("test")
    table = catalog.create_table("test.notice", schema=pa.schema([
        pa.field("desertion_no", pa.string(), nullable=False),
        pa.field("color_tag", pa.list_(pa.string())),
    ]))
    table.append(arrow(
        table,
        {"desertion_no": "update", "color_tag": ["black"]},
        {"desertion_no": "same", "color_tag": None},
        {"desertion_no": "untouched", "color_tag": ["white"]},
    ))
    with table.update_schema() as evolution:
        for field in FIELDS:
            evolution.add_column(field, StringType(), required=False)
    return catalog, table


def prepared_notice(table):
    return arrow(
        table,
        {"desertion_no": "update", "color_tag": ["black"], "health_chk": "checked"},
        {"desertion_no": "same"},
        {"desertion_no": "insert", "vaccination_chk": ""},
    )


def test_evolved_notice_upsert_projects_current_schema(evolved_notice):
    _, table = evolved_notice
    input_rows = prepared_notice(table)
    snapshot_id = table.current_snapshot().snapshot_id
    filtered = table.scan(row_filter=EqualTo("desertion_no", "update"))
    ordinary = filtered.to_arrow()
    pinned = filtered.use_ref("main").to_arrow()
    assert ordinary.column_names == input_rows.column_names
    assert all(ordinary[field].to_pylist() == [None] for field in FIELDS)
    assert set(FIELDS).isdisjoint(pinned.column_names)

    with pytest.raises(ValueError, match="Target schema's field names are not matching"):
        table.upsert(input_rows, join_cols=["desertion_no"])
    assert table.current_snapshot().snapshot_id == snapshot_id

    unchanged = upsert_notice(table, arrow(table, {"desertion_no": "same"}))
    assert (unchanged.rows_updated, unchanged.rows_inserted) == (0, 0)
    assert table.current_snapshot().snapshot_id == snapshot_id

    result = upsert_notice(table, input_rows)
    assert (result.rows_updated, result.rows_inserted) == (1, 1)
    stored = {row["desertion_no"]: row for row in table.scan().to_arrow().to_pylist()}
    assert len(stored) == len(table.scan().to_arrow()) == 4
    for row in input_rows.to_pylist():
        assert stored[row["desertion_no"]] == row
    assert stored["untouched"] == {"desertion_no": "untouched", "color_tag": ["white"], **dict.fromkeys(FIELDS)}

    committed = table.current_snapshot().snapshot_id
    rerun = upsert_notice(table, input_rows)
    assert (rerun.rows_updated, rerun.rows_inserted) == (0, 0)
    assert table.current_snapshot().snapshot_id == committed


@pytest.mark.parametrize("location", ["source", "target"])
def test_evolved_notice_upsert_refuses_duplicate_keys_before_commit(evolved_notice, location):
    _, table = evolved_notice
    row = {"desertion_no": "same"}
    if location == "target":
        # Seed duplicates using the schema captured by the old snapshot before evolving again.
        table.append(arrow(table, row))
        with table.update_schema() as evolution:
            evolution.add_column("other_optional", StringType())
    payload = arrow(table, row, row) if location == "source" else arrow(table, row)
    snapshot_id = table.current_snapshot().snapshot_id
    with pytest.raises(ValueError, match="Duplicate key"):
        upsert_notice(table, payload)
    assert table.current_snapshot().snapshot_id == snapshot_id


def test_real_runner_reuses_immutable_prepared_snapshot_after_schema_evolution(
    evolved_notice, tmp_path, monkeypatch, capsys,
):
    catalog, notice = evolved_notice
    history = catalog.create_table("test.history", schema=pa.schema([
        (field, pa.string()) for field in runner.HISTORY_KEYS
    ]))
    monkeypatch.setattr(runner, "NOTICE_NAME", "test.notice")
    monkeypatch.setattr(runner, "HISTORY_NAME", "test.history")
    monkeypatch.setattr(runner, "get_tables", lambda: (
        catalog, catalog.load_table("test.notice"), catalog.load_table("test.history"),
    ))
    monkeypatch.setattr(runner, "prepare_new", lambda *a, **kw: pytest.fail("must reuse PREPARED"))

    run_id = "20261118T030011-abcdef"
    prefix = "mock-silver"
    bronze, events = tmp_path / "bronze.json", tmp_path / "events.json"
    bronze.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    events.write_text("{}", encoding="utf-8")
    staging = tmp_path / "staging"
    notice_arrow = prepared_notice(notice)
    history_row = dict(zip(runner.HISTORY_KEYS, (run_id, "update", "2026-11-18", "CHANGED")))
    runner.store_snapshot(
        staging, run_id, [runner.sha256_file(bronze), runner.sha256_file(events)],
        notice, history, notice_arrow, arrow(history, history_row),
    )
    prepared_key = stage_key(prefix, run_id, "PREPARED")
    original = {f"{prefix}/run_id={run_id}/{file}": (staging / file).read_bytes() for file in SNAPSHOT_FILES}
    original[prepared_key] = json.dumps({"run_id": run_id, "stage": "PREPARED"}).encode()
    remote = dict(original)

    def get_object(*, Bucket, Key):
        if Key not in remote:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": BytesIO(remote[Key])}

    def put_object(*, Bucket, Key, Body, **kwargs):
        assert Key not in remote and kwargs["IfNoneMatch"] == "*"
        remote[Key] = Body

    s3 = Mock()
    s3.get_paginator.return_value.paginate.return_value = [
        {"CommonPrefixes": [{"Prefix": f"{prefix}/run_id={run_id}/"}]}
    ]
    s3.get_object.side_effect = get_object
    s3.put_object.side_effect = put_object
    monkeypatch.setattr(runner.boto3, "client", lambda *a, **kw: s3)
    args = SimpleNamespace(
        bronze_manifest=bronze, event_manifest=events, environment="test",
        apply=True, journal_dir=tmp_path / "recovery",
        s3_state_bucket="mock-bucket", s3_state_prefix=prefix,
    )
    runner.execute(args)
    assert "PREPARED_SNAPSHOT: REUSED" in capsys.readouterr().out
    assert current_stage(s3, args.s3_state_bucket, prefix, run_id) == "COMPLETED"
    assert {key: remote[key] for key in original} == original
    assert catalog.load_table("test.history").scan().to_arrow().to_pylist() == [history_row]
    notice_id = catalog.load_table("test.notice").current_snapshot().snapshot_id
    writes = s3.put_object.call_count

    runner.execute(args)
    assert "REUSED_ALREADY_VERIFIED" in capsys.readouterr().out
    assert catalog.load_table("test.notice").current_snapshot().snapshot_id == notice_id
    assert s3.put_object.call_count == writes



def test_notice_snapshot_records_bronze_run_id(evolved_notice):
    _, table = evolved_notice
    run_id = "test_bronze_run_001"

    result = upsert_notice(
        table,
        prepared_notice(table),
        run_id=run_id,
    )

    assert (result.rows_updated, result.rows_inserted) == (1, 1)

    table.refresh()
    snapshot = table.current_snapshot()

    assert snapshot is not None
    assert snapshot.summary is not None
    assert snapshot.summary.additional_properties.get(
        "bronze_run_id"
    ) == run_id
