"""prod → _test 고정 시점 복사. 로컬 SQLite 카탈로그 + 임시 폴더로 확인한다(AWS 없음)."""
from __future__ import annotations

from array import array
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("pyiceberg")
pytest.importorskip("sqlalchemy")

from rebom.common import gold_catalog as gc
from rebom.embedding import gold_snapshot as snap, gold_store as gs
from rebom.embedding.log_copy import CopyError

KST = timezone(timedelta(hours=9))
UNTIL = datetime(2026, 11, 24, tzinfo=KST)
BEFORE, AFTER = UNTIL - timedelta(seconds=1), UNTIL  # 기준 시각 자체는 복사하지 않는다
VERSION = "gemini-embedding-2:768/prep"


def image(sha, at=BEFORE, value=0.5):
    return {"subject_type": "photo", "subject_key": sha, "task": "image_embedding", "model": "gemini-embedding-2:768",
            "prompt_version": "prep", "source_hash": sha, "embedding": array("f", [value] * 768), "gold_run_id": "r",
            "gold_updated_at": at}


def log(sha, at=BEFORE, status="OK", task="image_embedding"):
    return {"subject_type": "photo", "subject_key": sha, "task": task, "version": VERSION, "input_hash": sha,
            "status": status, "attempts": 1, "gold_run_id": "r", "gold_updated_at": at, "detail": "{}"}


def request(i, at=BEFORE, task="image_embedding"):
    return {"request_id": f"g{i}", "subject_type": "photo", "subject_key": f"s{i}", "task": task, "cost_usd": 0.0001,
            "requested_at": at, "gold_run_id": "r", "dt": "2026-11-23", "batch_size": 1}


def canary(run, at=BEFORE):
    return {"gold_run_id": run, "task": "image_embedding", "model": "gemini-embedding-2:768", "checked_at": at,
            "passed": True}


@pytest.fixture
def world(tmp_path):
    from pyiceberg.catalog.sql import SqlCatalog

    catalog = SqlCatalog("t", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    prod = gc.GoldEnvironment("prod", "rebom_gold", "", f"file://{tmp_path}/wh/prod")
    test = gc.GoldEnvironment("test", "rebom_gold", "_test", f"file://{tmp_path}/wh/test")
    gc.create_tables(test, catalog)
    gc.create_tables(prod, catalog, group="embedding")  # 지금 prod: 결과 표 + 옛 로그, 공용 로그 없음
    for legacy, shared in ((gc.LEGACY_EMBEDDING_INFERENCE_LOG_TABLE, gc.INFERENCE_LOG_TABLE),
                           (gc.LEGACY_EMBEDDING_REQUEST_LOG_TABLE, gc.REQUEST_LOG_TABLE)):
        schema, spec = gc.schemas()[shared]
        catalog.create_table(prod.table(legacy), schema=schema, partition_spec=spec)

    class World:
        pass

    w = World()
    w.catalog, w.prod, w.test = catalog, prod, test

    def add(env, base, rows):
        t = catalog.load_table(env.table(base))
        t.append(gs.table_arrow(t, rows, vector=base == gc.IMAGE_EMBEDDING_TABLE))

    w.add = add
    w.rows = lambda env, base: catalog.load_table(env.table(base)).scan().to_arrow().to_pylist()
    w.plan = lambda until=UNTIL: snap.plan(catalog, prod, test, until)
    w.verify = lambda: snap.verify(catalog, prod, test, UNTIL)
    add(prod, gc.IMAGE_EMBEDDING_TABLE, [image("a"), image("b", value=0.25), image("new", AFTER)])
    add(prod, gc.LEGACY_EMBEDDING_INFERENCE_LOG_TABLE,
        [log("a"), log("b"), log("z", status="BLANK"), log("new", AFTER)])
    add(prod, gc.LEGACY_EMBEDDING_REQUEST_LOG_TABLE, [request(1), request(2, AFTER)])
    add(prod, gc.EMBEDDING_CANARY_TABLE, [canary("c1"), canary("c2", AFTER)])
    # test 에 이미 있는 것: 앞서 옮긴 사진 a, 태깅 로그
    add(test, gc.IMAGE_EMBEDDING_TABLE, [image("a")])
    add(test, gc.INFERENCE_LOG_TABLE, [log("a", task="photo_trait", status="FAILED")])
    add(test, gc.REQUEST_LOG_TABLE, [request(9, task="photo_trait")])
    return w


def test_copies_only_rows_before_until_and_keeps_prod_and_tagging(world):
    before = {b: world.rows(world.prod, b) for b in (gc.IMAGE_EMBEDDING_TABLE, gc.LEGACY_EMBEDDING_INFERENCE_LOG_TABLE,
                                                     gc.LEGACY_EMBEDDING_REQUEST_LOG_TABLE, gc.EMBEDDING_CANARY_TABLE)}
    parts = world.plan()
    assert [(p.spec.name, p.source_rows, p.after, len(p.add)) for p in parts] == [
        ("image", 2, 1, 1), ("inference", 3, 1, 3), ("request", 1, 1, 1), ("canary", 1, 1, 1)]
    assert len(world.rows(world.test, gc.IMAGE_EMBEDDING_TABLE)) == 1 and not world.verify()  # 계획만으로는 쓰지 않음
    snap.write(parts)
    assert world.verify()
    snap.write(world.plan())  # 다시 해도 그대로
    images = {r["subject_key"]: r for r in world.rows(world.test, gc.IMAGE_EMBEDDING_TABLE)}
    assert set(images) == {"a", "b"} and images["b"]["embedding"] == [0.25] * 768
    logs = world.rows(world.test, gc.INFERENCE_LOG_TABLE)
    assert sorted((r["task"], r["subject_key"], r["status"]) for r in logs) == [
        ("image_embedding", "a", "OK"), ("image_embedding", "b", "OK"), ("image_embedding", "z", "BLANK"),
        ("photo_trait", "a", "FAILED")]
    assert sorted(r["request_id"] for r in world.rows(world.test, gc.REQUEST_LOG_TABLE)) == ["g1", "g9"]
    assert [r["gold_run_id"] for r in world.rows(world.test, gc.EMBEDDING_CANARY_TABLE)] == ["c1"]
    assert {b: world.rows(world.prod, b) for b in before} == before  # prod 는 그대로


def test_prod_rows_added_later_do_not_change_snapshot(world):
    snap.write(world.plan())
    world.add(world.prod, gc.IMAGE_EMBEDDING_TABLE, [image("later", AFTER + timedelta(days=1))])
    world.add(world.prod, gc.LEGACY_EMBEDDING_INFERENCE_LOG_TABLE, [log("later", AFTER + timedelta(days=1))])
    assert world.verify() and not any(p.add for p in world.plan())


def test_rows_made_in_test_are_kept(world):
    snap.write(world.plan())
    t = world.catalog.load_table(world.test.table(gc.INFERENCE_LOG_TABLE))
    t.upsert(gs.table_arrow(t, [log("z", AFTER, "OK")]), join_cols=list(gc.INFERENCE_LOG_KEYS))
    inference = world.plan()[1]
    assert not inference.add and inference.keep_newer == 1


def test_stops_when_same_key_has_other_vector(world):
    world.add(world.test, gc.IMAGE_EMBEDDING_TABLE, [image("b", value=0.9)])
    with pytest.raises(CopyError, match="같은 키의 값이 다름"):
        world.plan()


def test_stops_when_logs_and_vectors_disagree(world):
    world.add(world.prod, gc.LEGACY_EMBEDDING_INFERENCE_LOG_TABLE, [log("no-vector")])
    with pytest.raises(CopyError, match="사진이 다름"):
        world.plan()


def test_never_writes_to_prod_and_needs_timezone(world):
    with pytest.raises(CopyError, match="prod 에는 쓰지 않습니다"):
        snap.plan(world.catalog, world.test, world.prod, UNTIL)
    with pytest.raises(CopyError, match="시간대"):
        world.plan(datetime(2026, 11, 24))


def test_cli_checks_only_by_default(world, monkeypatch, capsys):
    monkeypatch.setattr("rebom.storage.silver.aws_catalog.get_aws_catalog", lambda **k: world.catalog)
    monkeypatch.setattr(gc, "get_gold_environment", lambda name: {"prod": world.prod, "test": world.test}[name])
    args = ["--until", "2026-11-24T00:00:00+09:00"]
    assert snap.main(args) == 0
    assert "확인만 했습니다" in capsys.readouterr().out and len(world.rows(world.test, gc.IMAGE_EMBEDDING_TABLE)) == 1
    assert snap.main([*args, "--write"]) == 0
    assert "검증 통과" in capsys.readouterr().out and len(world.rows(world.test, gc.IMAGE_EMBEDDING_TABLE)) == 2
