"""Gold 임베딩 묶음 표 (11-20 결정): 로컬 SQLite 카탈로그 + 임시 폴더로 확인한다(AWS 없음).

- 사진 벡터는 sha256(subject_key) × 모델 × 전처리 버전당 한 행, 768차원 list<float> 그대로 읽힌다.
- --group embedding 은 임베딩 결과 표만 만들고 태깅 결과·공용 로그는 만들거나 바꾸지 않는다.
- 처리·요청 기록은 태깅·임베딩이 같이 쓰는 공용 로그 2개다 (11-24 결정). 옛 임베딩 로그는 만들지 않는다.
"""
from datetime import UTC, datetime

import pytest

pytest.importorskip("pyiceberg")
pytest.importorskip("sqlalchemy")

from rebom.common import gold_catalog as gc

SHA = "a" * 64


@pytest.fixture()
def world(tmp_path):
    from pyiceberg.catalog.sql import SqlCatalog

    catalog = SqlCatalog("t", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    env = gc.GoldEnvironment("unit", "rebom_gold", "_unit", f"file://{tmp_path}/wh/gold")
    return catalog, env


def _arrow(table, rows):
    import pyarrow as pa

    return pa.Table.from_pylist(rows, schema=table.schema().as_arrow())


def test_group_embedding_creates_only_embedding_tables(world):
    catalog, env = world
    made = gc.create_tables(env, catalog, group="embedding")
    assert sorted(made) == sorted(f"만듦 {env.table(b)}" for b in gc.GROUPS["embedding"])
    names = {".".join(t) for t in catalog.list_tables(env.database)}
    assert not {env.table(b) for g in ("tagging", "log") for b in gc.GROUPS[g]} & names
    bases = [b for g in gc.GROUPS.values() for b in g]
    assert sorted(bases) == sorted(gc.schemas()) and len(bases) == len(set(bases))  # 묶음끼리 겹치거나 빠진 표가 없다
    # 다른 묶음을 나중에 만들면 그 묶음의 표만 생긴다. canary 표는 대상 칸이 없어도 옛 스키마로 보지 않는다
    for group in ("tagging", "log"):
        assert sorted(gc.create_tables(env, catalog, group=group)) == sorted(
            f"만듦 {env.table(b)}" for b in gc.GROUPS[group])
    for group in ("execution", "registry"):
        assert gc.create_tables(env, catalog, group=group) == [
            f"만듦 {env.table(b)}" for b in gc.GROUPS[group]
        ]
    assert gc.create_tables(env, catalog) == []
    with pytest.raises(ValueError, match="UNKNOWN_GOLD_GROUP"):
        gc.create_tables(env, catalog, group="nope")


def test_register_gold_versions_without_replacing_history(world):
    from rebom.common.gold_version_registry import register_version, resolve_version

    catalog, env = world
    name = env.table(gc.VERSION_REGISTRY_TABLE)
    assert gc.create_tables(env, catalog, group="registry") == [f"만듦 {name}"]
    assert {".".join(table) for table in catalog.list_tables(env.database)} == {name}

    task = "image_embedding"
    v1 = {"model_id": "gemini-embedding-2", "dim": 768, "preprocess_version": "prep-v1"}
    v2 = dict(v1, preprocess_version="prep-v2")
    assert register_version(catalog, env, task, "image_embedding@1", v1)
    assert not register_version(catalog, env, task, "image_embedding@1", dict(reversed(list(v1.items()))))
    with pytest.raises(ValueError, match="different config"):
        register_version(catalog, env, task, "image_embedding@1", v2)
    assert register_version(catalog, env, task, "image_embedding@2", v2)
    assert resolve_version(catalog, env, task, "image_embedding@1") == v1
    assert resolve_version(catalog, env, task, "image_embedding@2") == v2
    with pytest.raises(ValueError, match="Unregistered"):
        resolve_version(catalog, env, task, "image_embedding@3")
    with pytest.raises(ValueError, match="task@N"):
        register_version(catalog, env, task, "photo_trait@1", v1)


def test_image_embedding_vector_round_trip_and_upsert(world):
    catalog, env = world
    gc.create_tables(env, catalog, group="embedding")
    table = catalog.load_table(env.table(gc.IMAGE_EMBEDDING_TABLE))
    ts = datetime.now(UTC)
    vec = [i / 1000 for i in range(768)]

    def row(v, run):
        return {"subject_type": "photo", "subject_key": SHA, "task": "image_embedding", "model": "gemini-embedding-2:768",
                "prompt_version": "exif-rgb-max1024-jpeg90", "source_hash": SHA, "embedding": v,
                "gold_run_id": run, "gold_updated_at": ts}

    table.upsert(_arrow(table, [row(vec, "r1")]), join_cols=list(gc.IMAGE_EMBEDDING_KEYS))
    table.upsert(_arrow(table, [row([0.5] * 768, "r2")]), join_cols=list(gc.IMAGE_EMBEDDING_KEYS))
    rows = table.scan().to_arrow().to_pylist()
    assert len(rows) == 1 and rows[0]["gold_run_id"] == "r2"  # 같은 키는 한 행
    assert len(rows[0]["embedding"]) == 768 and rows[0]["embedding"][0] == pytest.approx(0.5)
    other = row(vec, "r3") | {"model": "gemini-embedding-3:768"}  # 다른 모델은 다른 행
    table.upsert(_arrow(table, [other]), join_cols=list(gc.IMAGE_EMBEDDING_KEYS))
    got = {r["model"]: r for r in table.scan().to_arrow().to_pylist()}
    assert set(got) == {"gemini-embedding-2:768", "gemini-embedding-3:768"}
    assert got["gemini-embedding-3:768"]["embedding"][1] == pytest.approx(0.001, rel=1e-6)  # float32 로 저장


def test_text_embedding_keeps_masked_card_and_history(world):
    catalog, env = world
    gc.create_tables(env, catalog, group="embedding")
    table = catalog.load_table(env.table(gc.TEXT_EMBEDDING_TABLE))
    ts = datetime.now(UTC)

    def row(card_hash, text):
        return {"subject_type": "animal", "subject_key": "411300202500003", "task": "text_embedding",
                "model": "gemini-embedding-2:768", "prompt_version": "A2", "source_hash": card_hash,
                "input_text": text, "source_update_dt": "2026-11-20 10:00:00.0", "embedding": [0.0] * 768,
                "gold_run_id": "t1", "gold_updated_at": ts}

    table.upsert(_arrow(table, [row("h1", "연락 [번호]")]), join_cols=list(gc.TEXT_EMBEDDING_KEYS))
    table.upsert(_arrow(table, [row("h2", "내용이 바뀐 카드")]), join_cols=list(gc.TEXT_EMBEDDING_KEYS))
    rows = sorted(table.scan().to_arrow().to_pylist(), key=lambda r: r["source_hash"])
    assert [r["source_hash"] for r in rows] == ["h1", "h2"]  # 카드가 바뀌어도 이전 행은 남는다
    assert rows[0]["input_text"] == "연락 [번호]"


def test_shared_logs_have_embedding_columns_and_no_legacy_tables(world):
    """11-24 결정: 처리·요청 기록은 태깅·임베딩이 같이 쓴다. 임베딩 칸은 비울 수 있고, 옛 임베딩 로그는 만들지 않는다."""
    catalog, env = world
    gc.create_tables(env, catalog)
    inference, request = (catalog.load_table(env.table(b)) for b in (gc.INFERENCE_LOG_TABLE, gc.REQUEST_LOG_TABLE))
    assert inference.schema().identifier_field_names() == set(gc.INFERENCE_LOG_KEYS)
    assert [f.name for f in request.spec().fields] == ["dt"]
    assert not inference.schema().find_field("detail").required
    assert not request.schema().find_field("batch_size").required and not request.schema().find_field("version").required
    names = {t[-1] for t in catalog.list_tables(env.database)}
    assert not names & {gc.LEGACY_EMBEDDING_INFERENCE_LOG_TABLE + env.suffix, gc.LEGACY_EMBEDDING_REQUEST_LOG_TABLE + env.suffix}


def test_existing_tagging_logs_get_embedding_columns(world):
    """이미 있는 태깅 로그(되돌린 request_scope·item_count 가 남은 표 포함)에 칸만 더하고 행은 그대로 둔다."""
    from pyiceberg.schema import Schema
    from pyiceberg.types import IntegerType, NestedField, StringType

    catalog, env = world
    catalog.create_namespace(env.database)
    schemas = gc.schemas()
    inference, spec = schemas[gc.INFERENCE_LOG_TABLE]
    request, request_spec = schemas[gc.REQUEST_LOG_TABLE]
    old_inference = Schema(*[f for f in inference.fields if f.name != "detail"], identifier_field_ids=[1, 2, 3, 4, 5])
    old_request = Schema(*[f for f in request.fields if f.field_id <= 27],
                         NestedField(28, "request_scope", StringType(), required=False),
                         NestedField(29, "item_count", IntegerType(), required=False))
    catalog.create_table(env.table(gc.INFERENCE_LOG_TABLE), schema=old_inference, partition_spec=spec)
    old = catalog.create_table(env.table(gc.REQUEST_LOG_TABLE), schema=old_request, partition_spec=request_spec)
    old.append(_arrow(old, [{"request_id": "q", "task": "photo_trait", "cost_usd": 0.1,
                             "requested_at": datetime.now(UTC), "dt": "2026-11-23"}]))
    assert gc.create_tables(env, catalog, group="log") == [
        f"칸 추가 {env.table(gc.INFERENCE_LOG_TABLE)}: detail",
        f"칸 추가 {env.table(gc.REQUEST_LOG_TABLE)}: batch_size, version"]
    table = catalog.load_table(env.table(gc.REQUEST_LOG_TABLE))
    table.append(_arrow(table, [{"request_id": "e", "task": "image_embedding", "cost_usd": 0.2, "batch_size": 1,
                                 "requested_at": datetime.now(UTC), "dt": "2026-11-24"}]))
    rows = {r["request_id"]: r for r in table.scan().to_arrow().to_pylist()}
    assert rows["q"]["batch_size"] is None and rows["e"]["batch_size"] == 1
    assert rows["e"]["request_scope"] is None and rows["e"]["item_count"] is None  # 남은 칸과 섞이지 않음
    assert gc.create_tables(env, catalog, group="log") == []


def test_group_embedding_does_not_touch_existing_tagging_tables(world):
    """태깅이 먼저 만든 test 표가 있어도 --group embedding 은 그 표를 바꾸지 않는다."""
    catalog, env = world
    existing = gc.GROUPS["tagging"] + gc.GROUPS["log"]
    for group in ("tagging", "log"):
        gc.create_tables(env, catalog, group=group)
    before = {b: catalog.load_table(env.table(b)).metadata_location for b in existing}
    changed = gc.create_tables(env, catalog, group="embedding")
    assert sorted(changed) == sorted(f"만듦 {env.table(b)}" for b in gc.GROUPS["embedding"])
    assert {b: catalog.load_table(env.table(b)).metadata_location for b in existing} == before


def test_prod_and_test_locations():
    test, prod = gc.get_gold_environment("test"), gc.get_gold_environment("prod")
    assert test.table(gc.IMAGE_EMBEDDING_TABLE) == "rebom_gold.gold_image_embedding_test"
    assert test.location(gc.IMAGE_EMBEDDING_TABLE).endswith("/gold/_test/iceberg/gold_image_embedding_test")
    assert prod.table(gc.IMAGE_EMBEDDING_TABLE) == "rebom_gold.gold_image_embedding"
    assert prod.location(gc.IMAGE_EMBEDDING_TABLE).endswith("/gold/iceberg/gold_image_embedding")
