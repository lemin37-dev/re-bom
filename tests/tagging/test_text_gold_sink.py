"""글 태그 Gold 쓰기: 로컬 SQLite 카탈로그 + 임시 폴더로 Iceberg 동작 확인(AWS 없음).

- 글 태그 교체는 (source=jev, animal, 공고번호, 작업, 버전, source_hash) 범위만이다.
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

P, V = rtt.tt.TASK_PERSONALITY, "text-personality-v1"


@pytest.fixture()
def sink(tmp_path):
    from pyiceberg.catalog.sql import SqlCatalog

    catalog = SqlCatalog("t", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    env = gc.GoldEnvironment("unit", "rebom_gold", "_unit", f"file://{tmp_path}/wh/gold")
    gc.create_tables(env, catalog)
    return rtt.TextGoldSink(catalog, env)


def tag(key, code, h, ts, version=V, task=P, subject="animal", source="jev"):
    return {"subject_type": subject, "subject_key": key, "tag_code": code, "source": source, "tagger_version": "m",
            "task": task, "model": "m", "prompt_version": version, "source_hash": h, "searchable": True, "displayable": True,
            "rank": None, "similarity": 90, "detail": "{}", "gold_run_id": "r", "gold_updated_at": ts}


def log(key, h, attempts, ts, run="r"):
    return {"subject_type": "animal", "subject_key": key, "task": P, "version": V, "input_hash": h, "status": "OK",
            "attempts": attempts, "tag_count": 1, "last_error": None, "gold_run_id": run, "gold_updated_at": ts}


def keys(sink):
    return sorted((r["subject_key"], r["prompt_version"], r["source_hash"], r["tag_code"]) for r in sink.tag.scan().to_arrow().to_pylist())


def test_new_text_adds_rows_and_keeps_previous(sink):
    ts = datetime.now(UTC)
    sink.tag.append(sink._arrow(sink.tag, [tag("sha1", "ear.drop", "sha1", ts, "trait-vision-v10", "photo_trait", "photo", "photo"),
                                           tag("1", "personality.gentle", "old", ts, "text-personality-v0")]))
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
        ("sha1", "trait-vision-v10", "sha1", "ear.drop"),
    ]
    assert len(sink.read_inference_log()) == 3  # (1, A), (2, B), (1, C)


def test_same_text_rewrite_replaces_only_that_text(sink):
    ts = datetime.now(UTC)
    sink.write([("1", P, V, "A")], [tag("1", "personality.gentle", "A", ts)], [log("1", "A", 1, ts)], [])
    sink.write([("1", P, V, "A")], [tag("1", "personality.gentle", "A", ts)], [log("1", "A", 2, ts, run="r2")], [])
    assert keys(sink) == [("1", V, "A", "personality.gentle")]  # 중복 없음
    logs = sink.read_inference_log()
    assert len(logs) == 1 and logs[0]["attempts"] == 2 and logs[0]["gold_run_id"] == "r2"
