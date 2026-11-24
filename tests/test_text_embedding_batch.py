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

    assert execute(write=False) == {"cards": 2, "planned": 2, "written": 0}
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

    from rebom.common.silver_input import load_ready
    import io
    import json

    notice_table = catalog.load_table(silver.notice_table)
    photo_table = catalog.load_table(silver.image_table)
    record = {
        "publish_id": "p1", "status": "READY", "bronze_run_id": "r1",
        "notice_run_id": "r1", "photo_run_id": "photo-r1",
        "notice_table": silver.notice_table, "photo_table": silver.image_table,
        "notice_snapshot_id": notice_table.current_snapshot().snapshot_id,
        "photo_snapshot_id": photo_table.current_snapshot().snapshot_id,
    }
    class S3:
        def get_object(self, **kwargs):
            assert kwargs == {"Bucket": "test", "Key": "publish/p1.json"}
            return {"Body": io.BytesIO(json.dumps(record).encode())}

    published = load_ready(S3(), "test", "publish", "p1")
    notice_table.overwrite(pa.Table.from_pylist([
        {"desertion_no": "new", "status_group": "PROTECTING", "breed_name_raw": "새 공고", "info_text": "새 글"},
    ], schema=notice_table.schema().as_arrow()))
    photo_table.overwrite(pa.Table.from_pylist([
        {"desertion_no": "1", "image_slot": 1, "sha256": "new", "original_s3_key": "new-photo"},
    ], schema=photo_table.schema().as_arrow()))
    result = text_batch.run(catalog, gold, None, version, limit=10, write=True,
                            published=published, embedder_factory=Embedder)
    assert result["cards"] == 2 and result["written"] == 0
    [run_input] = catalog.load_table(gold.table(gc.RUN_INPUT_TABLE)).scan().to_arrow().to_pylist()
    assert run_input["publish_id"] == "p1" and run_input["gold_run_id"] == result["gold_run_id"]

    candidate = "text_embedding@2"
    assert register_version(catalog, gold, text_batch.TASK, candidate, {
        "model_id": gemini.MODEL, "dim": gemini.DEFAULT_DIM, "template_version": "A2", "tag_versions": versions,
    })
    result = text_batch.run(catalog, gold, None, candidate, limit=10, write=True,
                            published=published, embedder_factory=Embedder)
    assert result["written"] == 2
    rows = catalog.load_table(gold.table(gc.TEXT_EMBEDDING_TABLE)).scan().to_arrow().to_pylist()
    written = [row for row in rows if row["version"] == candidate]
    assert {row["subject_key"] for row in written} == {"1", "2"}
    assert all(row["gold_run_id"] == result["gold_run_id"] for row in written)
    assert "복슬복슬" in next(row["input_text"] for row in written if row["subject_key"] == "1")
    runs = catalog.load_table(gold.table(gc.RUN_INPUT_TABLE)).scan().to_arrow().to_pylist()
    [candidate_run] = [row for row in runs if row["gold_run_id"] == result["gold_run_id"]]
    assert candidate_run["publish_id"] == "p1"
    from pyiceberg.expressions import EqualTo

    run_filter = EqualTo("gold_run_id", result["gold_run_id"])
    requests = catalog.load_table(gold.table(gc.REQUEST_LOG_TABLE)).scan(row_filter=run_filter).to_arrow().to_pylist()
    logs = catalog.load_table(gold.table(gc.INFERENCE_LOG_TABLE)).scan(row_filter=run_filter).to_arrow().to_pylist()
    [request] = requests
    assert (request["task"], request["version"], request["batch_size"]) == (text_batch.TASK, candidate, 2)
    assert {row["subject_key"] for row in logs} == {"1", "2"}
    for row in logs:
        assert (row["task"], row["version"], row["status"]) == (text_batch.TASK, candidate, "OK")
        assert json.loads(row["detail"])["request_id"] == request["request_id"]

    record["status"] = "BLOCKED"
    with pytest.raises(ValueError, match="READY"):
        load_ready(S3(), "test", "publish", "p1")


def test_tag_index_accepts_origin_source_and_legacy_jev_only():
    """source 는 출처(photo·text·rule). 글 태그는 바뀔 때까지 jev 도 받고, 다른 출처는 받지 않는다."""
    from rebom.embedding import text_batch

    def row(source, subject="animal", code="personality.gentle"):
        return {"task": "text_personality", "source": source, "subject_type": subject, "subject_key": "1",
                "source_hash": "h", "tag_code": code, "searchable": True, "version": None, "prompt_version": "v"}

    index = text_batch._tag_index([row("text"), row("jev", code="personality.active"), row("photo", code="x"),
                                   row("text", subject="photo", code="y")], {"text_personality": "v"})
    assert index[("text_personality", "1", "h")] == {"personality.gentle", "personality.active"}
