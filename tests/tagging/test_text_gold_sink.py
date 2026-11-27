"""글 태그 Gold 쓰기: 로컬 SQLite 카탈로그 + 임시 폴더로 Iceberg 동작 확인(AWS 없음).

- 글 태그 교체는 (source=text, animal, 공고번호, 작업, 버전, source_hash) 범위만이다.
  같은 공고의 이전 글 행·이전 버전 행·사진 태그 행·다른 공고 행은 남는다(Gold 이전 버전 보존, 2026-11-20 결정).
- 같은 글을 다시 쓰면(실패 재시도·끊긴 실행) 그 글의 행만 교체되어 중복이 생기지 않는다.
- 처리 기록은 (subject_type, subject_key, task, version, input_hash) 로 upsert 된다.
"""
from datetime import UTC, datetime

import pytest

pytest.importorskip("pyiceberg")
pytest.importorskip("sqlalchemy")

from rebom.common import gold_catalog as gc
from rebom.tagging import run_text_tags as rtt

P, V, PV = rtt.tt.TASK_PERSONALITY, "text_personality@1", "text-personality-v1"  # 등록 버전, 질문지 버전


@pytest.fixture()
def sink(tmp_path):
    from pyiceberg.catalog.sql import SqlCatalog

    catalog = SqlCatalog("t", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    env = gc.GoldEnvironment("unit", "rebom_gold", "_unit", f"file://{tmp_path}/wh/gold")
    gc.create_tables(env, catalog)
    register(catalog, env)
    return rtt.TextGoldSink(catalog, env)


def register(catalog, env, configs=None):
    from rebom.common.gold_version_registry import register_version

    for task, version in rtt.REGISTERED.items():
        register_version(catalog, env, task, version, (configs or {}).get(task) or rtt.runtime_config(task))


def tag(key, code, h, ts, version=V, task=P, subject="animal", source="text", prompt=PV):
    return {"subject_type": subject, "subject_key": key, "tag_code": code, "source": source, "tagger_version": "m",
            "task": task, "model": "m", "prompt_version": prompt, "version": version, "source_hash": h, "searchable": True, "displayable": True,
            "rank": None, "similarity": 90, "detail": "{}", "gold_run_id": "r", "gold_updated_at": ts}


def log(key, h, attempts, ts, run="r"):
    return {"subject_type": "animal", "subject_key": key, "task": P, "version": V, "input_hash": h, "status": "OK",
            "attempts": attempts, "tag_count": 1, "last_error": None, "gold_run_id": run, "gold_updated_at": ts}


def keys(sink):
    return sorted((r["subject_key"], r["version"] or r["prompt_version"], r["source_hash"], r["tag_code"])
                  for r in sink.tag.scan().to_arrow().to_pylist())


def test_new_text_adds_rows_and_keeps_previous(sink):
    ts = datetime.now(UTC)
    sink.tag.append(sink._arrow(sink.tag, [tag("sha1", "ear.drop", "sha1", ts, "photo_trait@1", "photo_trait", "photo", "photo",
                                               "trait-vision-v10"),
                                           tag("1", "personality.gentle", "old", ts, None, prompt="text-personality-v0")]))
    # 공고 1 글 A, 공고 2 글 B
    sink.write([("1", P, V, "A"), ("2", P, V, "B")],
               [tag("1", "personality.gentle", "A", ts), tag("1", "personality.active", "A", ts), tag("2", "personality.wary", "B", ts)],
               [log("1", "A", 1, ts), log("2", "B", 1, ts)], [])
    # 공고 1 의 글이 C 로 바뀌어 다시 태깅: 글 A 의 행은 남고 글 C 의 행이 더해진다. 공고 2 는 그대로
    sink.write([("1", P, V, "C")], [tag("1", "personality.timid", "C", ts)], [log("1", "C", 1, ts, run="r2")], [])
    assert keys(sink) == [
        ("1", "text-personality-v0", "old", "personality.gentle"),
        ("1", V, "A", "personality.active"), ("1", V, "A", "personality.gentle"),
        ("1", V, "C", "personality.timid"),
        ("2", V, "B", "personality.wary"),
        ("sha1", "photo_trait@1", "sha1", "ear.drop"),
    ]
    assert len(sink.read_inference_log()) == 3  # (1, A), (2, B), (1, C)


def test_same_text_rewrite_replaces_only_that_text(sink):
    ts = datetime.now(UTC)
    sink.write([("1", P, V, "A")], [tag("1", "personality.gentle", "A", ts)], [log("1", "A", 1, ts)], [])
    sink.write([("1", P, V, "A")], [tag("1", "personality.gentle", "A", ts)], [log("1", "A", 2, ts, run="r2")], [])
    assert keys(sink) == [("1", V, "A", "personality.gentle")]  # 중복 없음
    logs = sink.read_inference_log()
    assert len(logs) == 1 and logs[0]["attempts"] == 2 and logs[0]["gold_run_id"] == "r2"



def test_read_inference_log_only_text_tasks(sink):
    """공용 처리 기록에 같은 공고의 글 임베딩 행이 있어도 글 태깅 행만 읽는다."""
    ts = datetime.now(UTC)
    other = {**log("1", "A", 1, ts), "task": "text_embedding", "version": "text_embedding@1"}
    sink.write([], [], [log("1", "A", 1, ts), other], [])
    assert [(r["task"], r["subject_key"]) for r in sink.read_inference_log()] == [(P, "1")]

def _silver(tmp_path):
    from pyiceberg.catalog.sql import SqlCatalog

    catalog = SqlCatalog("s", uri=f"sqlite:///{tmp_path}/s.db", warehouse=f"file://{tmp_path}/swh")
    catalog.create_namespace("rebom_silver")

    class Env:
        notice_table = "rebom_silver.notice"

    return catalog, Env


def test_read_silver_pins_snapshot_before_cutoff(tmp_path):
    """11/23 기준으로 고르면 그 뒤에 들어온 공고는 대상에 들지 않고, 읽은 스냅샷이 기록된다."""
    import time

    import pyarrow as pa

    catalog, Env = _silver(tmp_path)
    row = lambda no: pa.table({"desertion_no": [no], "status_group": ["PROTECTING"], "species_code": ["417000"],
                               "info_text": [f"글{no}"], "special_mark": [""]})
    table = catalog.create_table(Env.notice_table, schema=row("1").schema)
    table.append(row("1"))
    first = table.current_snapshot()
    time.sleep(0.01)  # 두 번째 스냅샷이 뒤 밀리초에 커밋되게
    table.append(row("2"))

    used: dict = {}
    cutoff = (datetime.fromtimestamp(first.timestamp_ms / 1000, UTC), True)
    rows = rtt.read_silver(catalog, Env, cutoff, used)
    assert [r["desertion_no"] for r in rows] == ["1"]
    assert used[Env.notice_table]["snapshot_id"] == first.snapshot_id

    used = {}
    assert sorted(r["desertion_no"] for r in rtt.read_silver(catalog, Env, None, used)) == ["1", "2"]
    assert used[Env.notice_table]["snapshot_id"] == catalog.load_table(Env.notice_table).current_snapshot().snapshot_id


def test_read_silver_uses_schema_of_pinned_snapshot(tmp_path):
    """info_text 칸이 생기기 전 스냅샷을 읽으면 special_mark 만 읽는다."""
    import time

    import pyarrow as pa
    from pyiceberg.types import StringType

    catalog, Env = _silver(tmp_path)
    old = pa.table({"desertion_no": ["1"], "status_group": ["PROTECTING"], "species_code": ["417000"], "special_mark": ["순함"]})
    table = catalog.create_table(Env.notice_table, schema=old.schema)
    table.append(old)
    first = table.current_snapshot()
    time.sleep(0.01)
    with table.update_schema() as u:
        u.add_column("info_text", StringType())
    table = catalog.load_table(Env.notice_table)
    table.append(pa.table({"desertion_no": ["2"], "status_group": ["PROTECTING"], "species_code": ["417000"],
                           "special_mark": [""], "info_text": ["활발"]}).cast(table.schema().as_arrow()))

    rows = rtt.read_silver(catalog, Env, (datetime.fromtimestamp(first.timestamp_ms / 1000, UTC), True))
    assert rows == [{"desertion_no": "1", "status_group": "PROTECTING", "species_code": "417000", "special_mark": "순함"}]
    assert {r["desertion_no"] for r in rtt.read_silver(catalog, Env)} == {"1", "2"}


def test_sink_refuses_unregistered_or_changed_config(tmp_path):
    """레지스트리에 없거나 등록 설정이 지금 코드와 다르면 Gold 에 쓰기 전에 멈춘다."""
    from pyiceberg.catalog.sql import SqlCatalog

    catalog = SqlCatalog("u", uri=f"sqlite:///{tmp_path}/u.db", warehouse=f"file://{tmp_path}/uw")
    env = gc.GoldEnvironment("unit", "rebom_gold", "_unit", f"file://{tmp_path}/uw/gold")
    gc.create_tables(env, catalog)
    with pytest.raises(ValueError, match="Unregistered"):
        rtt.TextGoldSink(catalog, env)
    register(catalog, env, {P: {"model_id": "typesafe/jev-1.14", "prompt_version": "text-personality-v2"}})
    with pytest.raises(ValueError, match="does not match"):
        rtt.TextGoldSink(catalog, env)


def test_sink_checks_candidate_version_registration(tmp_path):
    """--version 후보도 레지스트리에 등록돼 있어야 쓴다."""
    from pyiceberg.catalog.sql import SqlCatalog
    from rebom.common.gold_version_registry import register_version

    catalog = SqlCatalog("c", uri=f"sqlite:///{tmp_path}/c2.db", warehouse=f"file://{tmp_path}/cw")
    env = gc.GoldEnvironment("unit", "rebom_gold", "_unit", f"file://{tmp_path}/cw/gold")
    gc.create_tables(env, catalog)
    register(catalog, env)
    v = rtt.registered_versions(["text_personality@2"])
    with pytest.raises(ValueError, match="Unregistered"):
        rtt.TextGoldSink(catalog, env, versions=v)
    register_version(catalog, env, P, "text_personality@2", rtt.runtime_config(P))
    rtt.TextGoldSink(catalog, env, versions=v)
