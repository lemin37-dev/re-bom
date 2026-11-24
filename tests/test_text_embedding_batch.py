from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from rebom.common import gold_catalog as gc
from rebom.common.gold_version_registry import register_version
from rebom.embedding import gemini, text_batch, text_card


def test_gold_text_batch_uses_current_tags_and_only_embeds_changed_cards(tmp_path):
    pytest.importorskip("pyiceberg")
    pytest.importorskip("sqlalchemy")
    import pyarrow as pa
    from pyiceberg.catalog.sql import SqlCatalog

    catalog = SqlCatalog("text", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    gold = gc.GoldEnvironment("test", "rebom_gold", "_test", f"file://{tmp_path}/wh/gold")
    gc.create_tables(gold, catalog)
    catalog.create_namespace("silver")
    silver = SimpleNamespace(notice_table="silver.notice", image_table="silver.image")
    notices = pa.table({
        "desertion_no": ["1", "2", "3"], "status_group": ["PROTECTING", "PROTECTING", "CLOSED"],
        "breed_name_raw": ["믹스견", "믹스묘", "믹스견"], "info_text": ["온순", "활발", "온순"],
    })
    catalog.create_table(silver.notice_table, schema=notices.schema).append(notices)
    photos = pa.table({
        "desertion_no": ["1", "1"], "image_slot": [2, 1], "sha256": ["b", "a"],
        "original_s3_key": ["photo-b", "photo-a"],
    })
    catalog.create_table(silver.image_table, schema=photos.schema).append(photos)
    tags = catalog.load_table(gold.table(gc.TAG_TABLE))
    versions = {"photo_trait": "photo_trait@1", "text_personality": "text-personality-v1",
                "text_health": "text-health-v1"}
    version = "text_embedding@1"
    assert register_version(catalog, gold, text_batch.TASK, version, {
        "model_id": gemini.MODEL, "dim": gemini.DEFAULT_DIM, "template_version": "A2", "tag_versions": versions,
    })

    def tag(source, subject, key, input_hash, task, code, *, selected=None):
        return {
            "source": source, "subject_type": subject, "subject_key": key, "source_hash": input_hash, "task": task,
            "tag_code": code, "tagger_version": "test", "prompt_version": versions[task], "version": selected,
            "searchable": True,
        }

    current = text_card.text_sha256("온순")
    first_tags = [
        tag("photo", "photo", "a", "a", "photo_trait", "fur.fluffy", selected=versions["photo_trait"]),
        tag("photo", "photo", "b", "b", "photo_trait", "eye.sharp", selected=versions["photo_trait"]),
        tag("photo", "photo", "a", "a", "photo_trait", "ear.drop", selected="photo_trait@2"),
        tag("jev", "animal", "1", current, "text_personality", "personality.gentle"),
        tag("jev", "animal", "1", "old", "text_personality", "personality.timid"),
    ]
    tags.append(pa.Table.from_pylist(first_tags, schema=tags.schema().as_arrow()))
    inputs = []

    class Embedder:
        def __init__(self, dim):
            self.dim = dim

        def embed_texts_recorded(self, texts):
            inputs.append(texts)
            return [[1.0] * self.dim for _ in texts], {"requested_at": datetime.now(UTC), "request_id": f"r{len(inputs)}"}

    def execute(write=True):
        return text_batch.run(catalog, gold, silver, version, limit=10, write=write, embedder_factory=Embedder)

    planned = execute(write=False)
    assert (planned["cards"], planned["selected"], planned["planned"], planned["written"]) == (2, 2, 2, 0)
    assert planned["with_tags"] == {"photo_trait": 1, "text_personality": 1, "text_health": 0}
    assert planned["silver_as_of"] is None and set(planned["silver_snapshots"]) == {"silver.notice", "silver.image"}
    assert not inputs
    assert execute()["written"] == 2
    assert "복슬복슬" in inputs[0][0] and "성격 순함" in inputs[0][0]
    assert all(value not in inputs[0][0] for value in ("날카로운", "늘어진", "겁많음"))
    assert execute()["written"] == 0

    tags.append(pa.Table.from_pylist([
        tag("text", "animal", "1", current, "text_health", "health.none"),  # 출처 값(11/24). jev 도 받는다
    ], schema=tags.schema().as_arrow()))
    assert execute()["written"] == 1
    assert "건강정보 없음" in inputs[1][0]
    assert execute()["written"] == 0

    results = catalog.load_table(gold.table(gc.TEXT_EMBEDDING_TABLE)).scan().to_arrow().to_pylist()
    assert len(results) == 3
    assert {r["version"] for r in results} == {version}
    assert len({r["source_hash"] for r in results if r["subject_key"] == "1"}) == 2
    requests = catalog.load_table(gold.table(gc.REQUEST_LOG_TABLE)).scan().to_arrow().to_pylist()
    assert sorted(r["batch_size"] for r in requests) == [1, 2]
    logs = catalog.load_table(gold.table(gc.INFERENCE_LOG_TABLE)).scan().to_arrow().to_pylist()
    assert len(logs) == 3 and {r["status"] for r in logs} == {"OK"}


def test_tag_index_accepts_origin_source_and_legacy_jev_only():
    """source 는 출처(photo·text·rule). 글 태그는 바뀔 때까지 jev 도 받고, 다른 출처는 받지 않는다."""
    from rebom.embedding import text_batch

    def row(source, subject="animal", code="personality.gentle"):
        return {"task": "text_personality", "source": source, "subject_type": subject, "subject_key": "1",
                "source_hash": "h", "tag_code": code, "searchable": True, "version": None, "prompt_version": "v"}

    index = text_batch._tag_index([row("text"), row("jev", code="personality.active"), row("photo", code="x"),
                                   row("text", subject="photo", code="y")], {"text_personality": "v"})
    assert index[("text_personality", "1", "h")] == {"personality.gentle", "personality.active"}


@pytest.fixture
def pinned(tmp_path):
    """기준 시각 전(첫 스냅샷)과 뒤(둘째 스냅샷)의 Silver 가 다른 환경."""
    pytest.importorskip("pyiceberg")
    pytest.importorskip("sqlalchemy")
    import time as clock

    import pyarrow as pa
    from pyiceberg.catalog.sql import SqlCatalog

    catalog = SqlCatalog("text", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    gold = gc.GoldEnvironment("test", "rebom_gold", "_test", f"file://{tmp_path}/wh/gold")
    gc.create_tables(gold, catalog)
    catalog.create_namespace("silver")
    silver = SimpleNamespace(notice_table="silver.notice", image_table="silver.image")

    def notice(*rows):
        return pa.table({"desertion_no": [r[0] for r in rows], "status_group": [r[1] for r in rows],
                         "breed_name_raw": ["믹스견"] * len(rows), "info_text": [r[2] for r in rows]})

    def photo(*rows):
        return pa.table({"desertion_no": [r[0] for r in rows], "image_slot": [1] * len(rows),
                         "sha256": [r[1] for r in rows], "original_s3_key": ["k"] * len(rows)})

    notices = catalog.create_table(silver.notice_table, schema=notice(("1", "PROTECTING", "온순")).schema)
    images = catalog.create_table(silver.image_table, schema=photo(("1", "a")).schema)
    notices.append(notice(("1", "PROTECTING", "온순"), ("2", "PROTECTING", "활발")))
    images.append(photo(("1", "a")))
    clock.sleep(0.05)
    at = datetime.now(UTC)
    clock.sleep(0.05)
    notices.overwrite(notice(("1", "CLOSED", "온순"), ("2", "PROTECTING", "글이 바뀜"), ("9", "PROTECTING", "새 공고")))
    images.overwrite(photo(("1", "new")))
    versions = {"photo_trait": "trait-vision-v10", "text_personality": "text-personality-v1", "text_health": "text-health-v1"}
    register_version(catalog, gold, text_batch.TASK, "text_embedding@1", {
        "model_id": gemini.MODEL, "dim": gemini.DEFAULT_DIM, "template_version": "A2", "tag_versions": versions})

    def add_tags(*tags):
        table = catalog.load_table(gold.table(gc.TAG_TABLE))
        table.append(pa.Table.from_pylist([
            {"source": source, "subject_type": subject, "subject_key": key, "source_hash": h, "task": task, "tag_code": code,
             "tagger_version": "t", "prompt_version": versions[task], "searchable": True}
            for source, subject, key, h, task, code in tags], schema=table.schema().as_arrow()))

    sent = []

    class Embedder:
        def __init__(self, dim):
            self.dim = dim

        def embed_texts_recorded(self, texts):
            sent.extend(texts)
            return [[1.0] * self.dim for _ in texts], {"requested_at": datetime.now(UTC), "request_id": f"r{len(sent)}"}

    def run(**kwargs):
        return text_batch.run(catalog, gold, silver, "text_embedding@1",
                              **{"limit": 10, "write": True, "embedder_factory": Embedder, **kwargs})

    return SimpleNamespace(catalog=catalog, gold=gold, at=at, add_tags=add_tags, run=run, sent=sent, notices=notices, images=images)


CALM = text_card.text_sha256("온순")
FULL = (("photo", "photo", "a", "a", "photo_trait", "fur.fluffy"),
        ("text", "animal", "1", CALM, "text_personality", "personality.gentle"),
        ("text", "animal", "1", CALM, "text_health", "health.info"))


def test_silver_as_of_reads_both_tables_before_cutoff(pinned):
    import json

    pinned.add_tags(*FULL)
    result = pinned.run(silver_as_of=pinned.at.isoformat())
    assert result["written"] == 2 and result["with_tags"] == {"photo_trait": 1, "text_personality": 1, "text_health": 1}
    assert any("복슬복슬" in t and "성격 순함" in t for t in pinned.sent)  # 기준 시각의 사진(a)·글
    assert not any("새 공고" in t or "글이 바뀜" in t for t in pinned.sent)  # 뒤에 들어온 내용은 없음
    logs = pinned.catalog.load_table(pinned.gold.table(gc.INFERENCE_LOG_TABLE)).scan().to_arrow().to_pylist()
    first = {"silver.notice": pinned.notices.metadata.snapshots[0].snapshot_id,
             "silver.image": pinned.images.metadata.snapshots[0].snapshot_id}
    assert result["silver_snapshots"] == first
    assert all(json.loads(r["detail"])["silver_snapshots"] == first for r in logs)  # 어느 Silver 로 만들었는지
    current = pinned.run(write=False)
    assert current["cards"] == 2 and current["silver_snapshots"] != first  # 비우면 현재 Silver


def test_silver_cutoff_matches_photo_tagging_rule(pinned):
    at, inclusive = text_batch.silver_cutoff("2026-11-23")
    assert (at.isoformat(), inclusive) == ("2026-11-24T00:00:00+09:00", False)
    assert text_batch.silver_cutoff("2026-11-23T12:00:00")[0].isoformat() == "2026-11-23T12:00:00+09:00"
    assert text_batch.silver_cutoff(None) is None and text_batch.silver_cutoff("") is None
    with pytest.raises(ValueError, match="no snapshot before"):
        pinned.run(silver_as_of="2020-01-01")


def test_require_tags_makes_only_complete_cards_first(pinned):
    pinned.add_tags(*FULL)
    tasks = ("photo_trait", "text_personality", "text_health")
    plan = pinned.run(write=False, silver_as_of=pinned.at.isoformat(), require_tags=tasks, show=5)
    assert (plan["cards"], plan["selected"], plan["planned"]) == (2, 1, 1)
    assert [c["notice"] for c in plan["show"]] == ["1"] and "건강정보 있음" in plan["show"][0]["text"]
    assert pinned.sent == []
    assert pinned.run(silver_as_of=pinned.at.isoformat(), require_tags=tasks)["written"] == 1
    rest = pinned.run(silver_as_of=pinned.at.isoformat())  # 나중에 전체를 만들면 이미 만든 카드는 다시 보내지 않음
    assert rest["written"] == 1 and len(pinned.sent) == 2
    with pytest.raises(ValueError, match="require_tags"):
        pinned.run(write=False, require_tags=("photo_breed",))


def test_unknown_tag_source_stops_instead_of_dropping_tags(pinned):
    pinned.add_tags(*FULL[:2], ("info_text", "animal", "1", CALM, "text_health", "health.info"))
    with pytest.raises(ValueError, match="info_text"):
        pinned.run(silver_as_of=pinned.at.isoformat())
    assert pinned.sent == []
