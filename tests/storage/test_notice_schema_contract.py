import runpy
from pathlib import Path
from unittest.mock import Mock

import pyarrow as pa
import pytest
from pyiceberg.catalog import load_catalog
from pyiceberg.types import StringType

from rebom.storage.silver.apply_silver_pipeline import NOTICE_KEYS, prepare_arrow, upsert_notice
from rebom.storage.silver.prepare_notice_closure import prepare_notice_closure
from rebom.storage.silver.prepare_notice_delta import prepare_notice_delta
from rebom.storage.silver.transform_notice import transform_notice
from rebom.storage.silver.transform_status_history import transform_status_history


FIELDS = {
    "sfe_soci": "sfeSoci",
    "sfe_health": "sfeHealth",
    "health_chk": "healthChk",
    "vaccination_chk": "vaccinationChk",
}
SAMPLE = {
    "desertionNo": "TEST-NOTICE",
    "careRegNo": "TEST-SHELTER",
    "sfeSoci": "  friendly  ",
    "sfeHealth": "healthy",
    "healthChk": "",
    "vaccinationChk": "recorded",
    "specialMark": "test mark",
    "processState": "\ubcf4\ud638\uc911",
}


@pytest.fixture(params=["local", "aws_test"])
def notice_schema(request, monkeypatch, tmp_path):
    catalog = Mock()
    catalog.table_exists.return_value = False

    def create_table(identifier, *, schema, location=None):
        table = Mock()
        table.name.return_value = identifier
        table.location.return_value = location
        table.schema.return_value.fields = list(schema)
        return table

    catalog.create_table.side_effect = create_table
    catalog.list_tables.side_effect = lambda _: [
        tuple(call.args[0].split(".")) for call in catalog.create_table.call_args_list
    ]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("pyiceberg.catalog.load_catalog", Mock(return_value=catalog))
    monkeypatch.setattr("rebom.storage.silver.aws_catalog.get_aws_catalog", Mock(return_value=catalog))
    if request.param == "local":
        script, table = "rebom_create_silver_tables.py", "silver_animal_notice"
    else:
        script, table = "rebom_create_aws_silver_tables.py", "silver_animal_notice_b_test"
    runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/storage" / script))
    notice, = [call.kwargs["schema"] for call in catalog.create_table.call_args_list
               if call.args[0] == f"rebom_silver.{table}"]
    history, = [call.kwargs["schema"] for call in catalog.create_table.call_args_list
                if "status_history" in call.args[0]]
    assert set(FIELDS).isdisjoint(history.names)
    return notice


def test_notice_column_schema_accepts_transformed_rows(notice_schema):
    table = Mock()
    table.schema.return_value.as_arrow.return_value = notice_schema
    for field in FIELDS:
        assert notice_schema.field(field) == pa.field(field, pa.string(), nullable=True)

    row = transform_notice(SAMPLE)
    result, = prepare_arrow([row], table, NOTICE_KEYS, require_all_fields=True).to_pylist()
    assert {field: result[field] for field in FIELDS} == {
        field: SAMPLE[raw] for field, raw in FIELDS.items()
    }
    assert result["info_text"] == "friendly healthy test mark"


@pytest.mark.parametrize("provided", [False, True])
def test_missing_or_null_bronze_fields_are_null(provided):
    raw = {"desertionNo": "TEST-NOTICE"}
    if provided:
        raw.update(dict.fromkeys(FIELDS.values()))
    notice = transform_notice(raw)
    assert {field: notice[field] for field in FIELDS} == dict.fromkeys(FIELDS)
    assert notice["info_text"] is None


def test_new_fields_participate_in_notice_delta_without_changing_status_history():
    previous = transform_notice(SAMPLE)
    bronze = {"rows_by_kind": {"updated": [dict(SAMPLE, vaccinationChk="new value")]}}
    prepared = prepare_notice_delta(bronze, [previous])
    updated, = prepared["delta_rows"]
    assert prepared["update_count"] == 1
    assert updated["vaccination_chk"] == "new value"
    assert updated["info_text"] == previous["info_text"]
    assert prepare_notice_delta(bronze, [updated])["delta_rows"] == []

    history, _ = transform_status_history(
        [SAMPLE], [], "2026-11-21", "2026-11-22", confirmed_missing_ids=[SAMPLE["desertionNo"]]
    )
    closure, = [row for row in history if row["event_type"] == "CHANGED"]
    assert set(FIELDS).isdisjoint(closure)
    result = prepare_notice_closure([updated], {
        "ready": True,
        "resolved": {"confirmed_missing_ids": [SAMPLE["desertionNo"]]},
        "history_rows": [dict(closure, is_inferred=True, confirmed_by="complete_range")],
    })
    closed, = result["delta_rows"]
    assert closed["status_group"] == "CLOSED"
    assert {field: closed[field] for field in FIELDS} == {field: updated[field] for field in FIELDS}


def test_upsert_after_evolving_full_notice_schema_from_41_to_45_columns(tmp_path, notice_schema):
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    catalog = load_catalog(
        "notice_schema", type="sql",
        uri=f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}",
        warehouse=str(warehouse),
    )
    catalog.create_namespace("rebom_test")
    name = "rebom_test.notice"
    old_schema = pa.schema([field for field in notice_schema if field.name not in FIELDS])
    assert len(old_schema) == 41
    assert len(notice_schema) == 45
    table = catalog.create_table(name, schema=old_schema)
    seeds = [
        transform_notice(dict(SAMPLE, desertionNo=key, specialMark=f"mark-{key}"))
        for key in ("UPDATE", "SAME", "UNTOUCHED")
    ]
    legacy_rows = [{field: row[field] for field in old_schema.names} for row in seeds]
    table.append(prepare_arrow(legacy_rows, table, NOTICE_KEYS, require_all_fields=True))
    snapshot_id = table.current_snapshot().snapshot_id
    existing_fields = table.schema().fields

    updated = dict(seeds[0], health_chk="checked", vaccination_chk=None)
    inserted = transform_notice(dict(SAMPLE, desertionNo="INSERT"))
    unchanged = {**legacy_rows[1], **dict.fromkeys(FIELDS)}
    with pytest.raises(ValueError, match="Unexpected columns"):
        prepare_arrow([updated], table, NOTICE_KEYS, require_all_fields=True)

    with table.update_schema() as update:
        for field in FIELDS:
            update.add_column(field, StringType(), required=False)
    table = catalog.load_table(name)

    assert table.current_snapshot().snapshot_id == snapshot_id
    assert table.schema().fields[:len(existing_fields)] == existing_fields
    assert len(table.schema().fields) == 45
    before = {row["desertion_no"]: row for row in table.scan().to_arrow().to_pylist()}
    assert before == {
        row["desertion_no"]: {**row, **dict.fromkeys(FIELDS)} for row in legacy_rows
    }

    payload = prepare_arrow([updated, unchanged, inserted], table, NOTICE_KEYS, require_all_fields=True)
    result = upsert_notice(table, payload)
    assert (result.rows_updated, result.rows_inserted) == (1, 1)
    table = catalog.load_table(name)
    stored = table.scan().to_arrow().to_pylist()
    assert len(stored) == 4
    assert {row["desertion_no"]: row for row in stored} == {
        **before, "UPDATE": updated, "INSERT": inserted,
    }

    committed_snapshot = table.current_snapshot().snapshot_id
    rerun = upsert_notice(table, payload)
    assert (rerun.rows_updated, rerun.rows_inserted) == (0, 0)
    assert catalog.load_table(name).current_snapshot().snapshot_id == committed_snapshot
