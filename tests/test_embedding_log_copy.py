"""옛 임베딩 로그 ↔ 공용 로그 복사. 로컬 SQLite 카탈로그 + 임시 폴더로 확인한다(AWS 없음)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("pyiceberg")
pytest.importorskip("sqlalchemy")

from rebom.common import gold_catalog as gc
from rebom.embedding import gold_store as gs, log_copy as lc

AT = datetime(2026, 11, 24, 3, 12, tzinfo=timezone.utc)
VERSION = "gemini-embedding-2:768/prep"


def inference(sha, status="OK", task="image_embedding", at=AT, **extra):
    return {"subject_type": "photo", "subject_key": sha, "task": task, "version": VERSION, "input_hash": sha,
            "status": status, "attempts": 1, "gold_run_id": "r1", "gold_updated_at": at,
            "detail": f'{{"request_id": "{sha}"}}', **extra}


def request(i, task="image_embedding", run="r1", **extra):
    return {"request_id": f"gen-{i}", "subject_type": "photo", "subject_key": f"s{i}", "task": task, "cost_usd": 0.0001,
            "requested_at": AT, "gold_run_id": run, "dt": "2026-11-24", "batch_size": 1, "status": "OK", **extra}


@pytest.fixture
def world(tmp_path):
    from pyiceberg.catalog.sql import SqlCatalog

    catalog = SqlCatalog("t", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    env = gc.GoldEnvironment("unit", "rebom_gold", "_unit", f"file://{tmp_path}/wh/gold")
    gc.create_tables(env, catalog)
    for legacy, shared in lc.PAIRS:  # 전환 전 prod 표: 같은 칸, 요청 기록에 version 만 없음
        schema, spec = gc.schemas()[shared]
        table = catalog.create_table(env.table(legacy), schema=schema, partition_spec=spec)
        if shared == gc.REQUEST_LOG_TABLE:
            with table.update_schema(allow_incompatible_changes=True) as update:
                update.delete_column("version")

    class World:
        pass

    w = World()
    w.catalog, w.env = catalog, env

    def add(base, rows):
        t = catalog.load_table(env.table(base))
        t.append(gs.table_arrow(t, rows))

    def rows(base):
        return catalog.load_table(env.table(base)).scan().to_arrow().to_pylist()

    w.add, w.rows = add, rows
    w.plan = lambda **k: lc.plan(catalog, env, **k)
    w.verify = lambda **k: lc.verify(catalog, env, **k)
    return w


LEGACY_INF, LEGACY_REQ = gc.LEGACY_EMBEDDING_INFERENCE_LOG_TABLE, gc.LEGACY_EMBEDDING_REQUEST_LOG_TABLE


def fill(w):
    w.add(LEGACY_INF, [inference("a"), inference("b", "BLANK"), inference("c", "READ_ERROR")])
    w.add(LEGACY_REQ, [request(1), request(2), request(3, run="canary-1")])
    # 공용 로그에 이미 있는 태깅 행
    w.add(gc.INFERENCE_LOG_TABLE, [inference("a", "FAILED", task="photo_trait")])
    w.add(gc.REQUEST_LOG_TABLE, [request(9, task="photo_trait")])


def test_copy_adds_missing_rows_once_and_keeps_tagging(world):
    fill(world)
    inf, req = world.plan()
    assert (len(inf.add), len(req.add)) == (3, 3) and not inf.update
    assert inf.stats()["by_status"] == {"OK": 1, "BLANK": 1, "READ_ERROR": 1}
    assert req.stats()["cost_usd"] == pytest.approx(0.0003)
    assert len(world.rows(gc.INFERENCE_LOG_TABLE)) == 1  # 계획만으로는 쓰지 않음
    assert not world.verify()
    lc.write([inf, req])
    assert world.verify()
    lc.write(world.plan())  # 다시 해도 그대로
    inference_rows, request_rows = world.rows(gc.INFERENCE_LOG_TABLE), world.rows(gc.REQUEST_LOG_TABLE)
    assert len(inference_rows) == 4 and len(request_rows) == 4
    assert [r["status"] for r in inference_rows if r["task"] == "photo_trait"] == ["FAILED"]
    copied = {r["subject_key"]: r for r in inference_rows if r["task"] == "image_embedding"}
    assert copied == {r["subject_key"]: r for r in world.rows(LEGACY_INF)}  # 모든 칸이 같음
    assert all(r["batch_size"] == 1 and r["version"] is None for r in request_rows if r["task"] == "image_embedding")
    assert len(world.rows(LEGACY_INF)) == 3 and len(world.rows(LEGACY_REQ)) == 3  # 원본은 그대로


def test_second_copy_takes_newer_source_but_never_overwrites_newer_target(world):
    fill(world)
    lc.write(world.plan())
    # 전환 직전 실행: 옛 표에서 c 가 OK 로 바뀌고 d 가 생김
    t = world.catalog.load_table(world.env.table(LEGACY_INF))
    t.upsert(gs.table_arrow(t, [inference("c", "OK", at=AT + timedelta(days=1))]), join_cols=list(gc.INFERENCE_LOG_KEYS))
    world.add(LEGACY_INF, [inference("d")])
    # 전환 뒤 공용 로그에서 b 가 새로 쓰임
    t = world.catalog.load_table(world.env.table(gc.INFERENCE_LOG_TABLE))
    t.upsert(gs.table_arrow(t, [inference("b", "OK", at=AT + timedelta(days=2))]), join_cols=list(gc.INFERENCE_LOG_KEYS))
    inf, _ = world.plan()
    assert ([r["subject_key"] for r in inf.add], [r["subject_key"] for r in inf.update], inf.keep_newer) == (["d"], ["c"], 1)
    lc.write([inf])
    assert world.verify()
    got = {r["subject_key"]: r["status"] for r in world.rows(gc.INFERENCE_LOG_TABLE) if r["task"] == "image_embedding"}
    assert got == {"a": "OK", "b": "OK", "c": "OK", "d": "OK"}


def test_reverse_copies_only_embedding_rows(world):
    fill(world)
    lc.write(world.plan())
    world.add(gc.INFERENCE_LOG_TABLE, [inference("new")])
    world.add(gc.REQUEST_LOG_TABLE, [request(4)])
    inf, req = world.plan(reverse=True)
    assert [r["subject_key"] for r in inf.add] == ["new"] and [r["request_id"] for r in req.add] == ["gen-4"]
    lc.write([inf, req])
    assert world.verify(reverse=True)
    assert {r["task"] for r in world.rows(LEGACY_INF)} == {"image_embedding"} and len(world.rows(LEGACY_REQ)) == 4


def test_reverse_stops_when_legacy_would_lose_version(world):
    fill(world)
    world.add(gc.REQUEST_LOG_TABLE, [request(4, version="image_embedding@1")])
    with pytest.raises(lc.CopyError, match="version"):
        world.plan(reverse=True)


@pytest.mark.parametrize("bad, message", [
    (lambda w: w.add(LEGACY_INF, [inference("a", task="photo_trait")]), "task"),
    (lambda w: w.add(LEGACY_REQ, [request(1)]), "중복"),
    (lambda w: w.add(gc.INFERENCE_LOG_TABLE, [inference("a", "FAILED")]), "같은 시각인데 값이 다름"),
    (lambda w: w.add(gc.REQUEST_LOG_TABLE, [request(1, cost_usd=9.0)]), "같은 요청의 값이 다름"),
])
def test_stops_without_writing_on_unexpected_rows(world, bad, message):
    fill(world)
    bad(world)
    with pytest.raises(lc.CopyError, match=message):
        world.plan()


def test_cli_checks_only_by_default(world, monkeypatch, capsys):
    fill(world)
    monkeypatch.setattr("rebom.storage.silver.aws_catalog.get_aws_catalog", lambda **k: world.catalog)
    monkeypatch.setattr(gc, "get_gold_environment", lambda name: world.env)
    assert lc.main(["--gold-env", "test"]) == 0
    assert "확인만 했습니다" in capsys.readouterr().out and len(world.rows(gc.INFERENCE_LOG_TABLE)) == 1
    assert lc.main(["--gold-env", "test", "--write"]) == 0
    assert "검증 통과" in capsys.readouterr().out and len(world.rows(gc.INFERENCE_LOG_TABLE)) == 4
    world.add(LEGACY_REQ, [request(1)])
    assert lc.main(["--gold-env", "test", "--write"]) == 2
    assert "복사 중단" in capsys.readouterr().err
