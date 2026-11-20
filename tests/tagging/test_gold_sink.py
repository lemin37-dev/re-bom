"""Gold 쓰기: 로컬 SQLite 카탈로그 + 임시 폴더로 Iceberg 동작을 확인한다(AWS 없음).

- 다시 태깅하면 (공고, 질문지) 범위의 이전 태그를 통째 교체하고, 다른 공고·다른 질문지 태그는 그대로 둔다.
- 처리 기록은 (desertion_no, task, version, input_hash) 로 upsert 된다.
"""
from datetime import UTC, datetime

import pytest

pytest.importorskip("pyiceberg")
pytest.importorskip("sqlalchemy")

from rebom.common import gold_catalog as gc
from rebom.tagging import run_photo_tags as rpt


@pytest.fixture()
def sink(tmp_path):
    from pyiceberg.catalog.sql import SqlCatalog

    catalog = SqlCatalog("t", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    env = gc.GoldEnvironment("unit", "rebom_gold", "_unit", f"file://{tmp_path}/wh/gold")
    made = gc.create_tables(env, catalog)
    assert len(made) == 3
    assert gc.create_tables(env, catalog) == []  # 두 번째는 만들 것이 없다
    return rpt.GoldSink(catalog, env)


def tag(no, code, task, ts):
    return {
        "desertion_no": no, "tag_code": code, "source": "photo", "tagger_version": f"m@{task}", "task": task,
        "model": "m", "prompt_version": "v", "image_slot": 1, "source_hash": "h", "searchable": True,
        "displayable": True, "rank": None, "similarity": None, "detail": "[]", "gold_run_id": "r", "gold_updated_at": ts,
    }


def test_overwrite_scope_and_upsert(sink):
    ts = datetime.now(UTC)
    t, b = rpt.trait_tags.TASK, rpt.breed_tags.TASK
    sink.write(
        [("1", t), ("1", b), ("2", t)],
        [tag("1", "ear.prick", t, ts), tag("1", "fur.short", t, ts), tag("1", "breed.진도견", b, ts), tag("2", "ear.drop", t, ts)],
        [{"desertion_no": "1", "task": t, "version": "v", "input_hash": "h", "image_slot": 1, "status": "FAILED",
          "attempts": 1, "tag_count": 0, "last_error": "x", "gold_run_id": "r", "gold_updated_at": ts}],
        [],
    )
    # 공고 1 특징만 다시 태깅: 태그 없음(NO_RESULT)
    sink.write(
        [("1", t)], [],
        [{"desertion_no": "1", "task": t, "version": "v", "input_hash": "h", "image_slot": 1, "status": "NO_RESULT",
          "attempts": 2, "tag_count": 0, "last_error": None, "gold_run_id": "r2", "gold_updated_at": ts}],
        [{"request_id": "q", "desertion_no": "1", "task": t, "prompt_version": "v", "run_no": 1, "requested_model": "m",
          "response_model": "m2", "provider": "p", "canonical_slug": "c", "media_resolution": "MEDIA_RESOLUTION_MEDIUM",
          "prompt_tokens": 1294, "completion_tokens": 1, "reasoning_tokens": 0, "cost_usd": 0.0007, "latency_ms": 1,
          "attempts": 1, "status": "OK", "error_code": None, "raw_answer": "{}", "requested_at": ts, "gold_run_id": "r2", "dt": "2026-11-20"}],
    )
    tags = {(r["desertion_no"], r["tag_code"]) for r in sink.tag.scan().to_arrow().to_pylist()}
    assert tags == {("1", "breed.진도견"), ("2", "ear.drop")}
    logs = sink.read_inference_log()
    assert len(logs) == 1 and logs[0]["status"] == "NO_RESULT" and logs[0]["attempts"] == 2
    assert sink.requests.scan().to_arrow().num_rows == 1


def test_read_silver_filters_protecting_dogs(tmp_path):
    import pyarrow as pa
    from pyiceberg.catalog.sql import SqlCatalog

    catalog = SqlCatalog("s", uri=f"sqlite:///{tmp_path}/s.db", warehouse=f"file://{tmp_path}/wh")
    catalog.create_namespace("rebom_silver")
    notices = pa.table({
        "desertion_no": ["1", "2", "3", "4"],
        "species_code": ["417000", "417000", "422400", "417000"],
        "status_group": ["PROTECTING", "CLOSED", "PROTECTING", "PROTECTING"],
    })
    images = pa.table({
        "desertion_no": ["1", "1", "2", "3"],
        "image_slot": pa.array([1, 2, 1, 1], pa.int32()),
        "original_s3_key": ["k1", "k2", "k3", "k4"],
        "sha256": ["a" * 64, "b" * 64, "c" * 64, "d" * 64],
    })
    catalog.create_table("rebom_silver.notice", schema=notices.schema).append(notices)
    catalog.create_table("rebom_silver.image", schema=images.schema).append(images)

    class Env:
        notice_table = "rebom_silver.notice"
        image_table = "rebom_silver.image"

    dogs, rows = rpt.read_silver(catalog, Env)
    assert sorted(dogs) == ["1", "4"]
    assert {(r["desertion_no"], r["image_slot"]) for r in rows} == {("1", 1), ("1", 2)}
    targets, no_photo = rpt.select_targets(dogs, rows, [])
    assert [(t.desertion_no, t.image_slot, t.s3_key) for t in targets] == [("1", 1, "k1")] and no_photo == ["4"]


def test_create_tables_adds_missing_columns_to_existing_table(tmp_path):
    from pyiceberg.catalog.sql import SqlCatalog
    from pyiceberg.schema import Schema

    catalog = SqlCatalog("t", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    env = gc.GoldEnvironment("unit", "rebom_gold", "_unit", f"file://{tmp_path}/wh/gold")
    gc.create_tables(env, catalog)
    name = env.table(gc.REQUEST_LOG_TABLE)
    table = catalog.load_table(name)
    with table.update_schema() as update:
        update.delete_column("finish_reason")
        update.delete_column("temperature")

    changed = gc.create_tables(env, catalog)
    assert changed == [f"칸 추가 {name}: temperature, finish_reason"]
    fields = {f.name for f in catalog.load_table(name).schema().fields}
    assert {"temperature", "finish_reason", "native_finish_reason"} <= fields
    assert gc.create_tables(env, catalog) == []
    assert isinstance(catalog.load_table(name).schema(), Schema)
