"""Gold 임베딩 묶음 표 (11-20 결정): 로컬 SQLite 카탈로그 + 임시 폴더로 확인한다(AWS 없음).

- 사진 벡터는 sha256(subject_key) × 모델 × 전처리 버전당 한 행, 768차원 list<float> 그대로 읽힌다.
- --group embedding 은 임베딩 묶음 표만 만들고 태깅 묶음 표는 만들거나 바꾸지 않는다.
- 임베딩 처리·요청 기록 표는 태깅 기록 표와 같은 칸 + detail·batch_size (11-21 결정, UNION ALL 가능).
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
    assert not {env.table(b) for b in gc.GROUPS["tagging"]} & names
    assert not set(gc.GROUPS["tagging"]) & set(gc.GROUPS["embedding"])  # 묶음끼리 겹치는 표가 없다
    # 태깅 묶음을 나중에 만들면 태깅 표 3개만 새로 생긴다. canary 표는 대상 칸이 없어도 옛 스키마로 보지 않는다
    assert sorted(gc.create_tables(env, catalog, group="tagging")) == sorted(
        f"만듦 {env.table(b)}" for b in gc.GROUPS["tagging"])
    assert gc.create_tables(env, catalog) == []
    with pytest.raises(ValueError, match="UNKNOWN_GOLD_GROUP"):
        gc.create_tables(env, catalog, group="nope")


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


def test_embedding_logs_are_separate_and_union_compatible(world):
    """11-21 결정: 임베딩은 처리·요청 기록 표를 태깅과 따로 둔다. 칸은 태깅 표와 같고 임베딩 칸만 더한다."""
    catalog, env = world
    gc.create_tables(env, catalog)
    tables = {b: catalog.load_table(env.table(b)) for b in gc.schemas()}

    def cols(base):
        return [(f.name, str(f.field_type)) for f in tables[base].schema().fields]

    inf, einf = cols(gc.INFERENCE_LOG_TABLE), cols(gc.EMBEDDING_INFERENCE_LOG_TABLE)
    req, ereq = cols(gc.REQUEST_LOG_TABLE), cols(gc.EMBEDDING_REQUEST_LOG_TABLE)
    assert einf[:len(inf)] == inf and [n for n, _ in einf[len(inf):]] == ["detail"]
    assert ereq[:len(req)] == req and [n for n, _ in ereq[len(req):]] == ["batch_size"]
    # 태깅 기록 표는 PR #51 그대로 (임베딩 칸이 없다)
    assert "detail" not in dict(inf) and "batch_size" not in dict(req)
    assert tables[gc.EMBEDDING_INFERENCE_LOG_TABLE].schema().identifier_field_names() == set(gc.INFERENCE_LOG_KEYS)
    assert [f.name for f in tables[gc.EMBEDDING_REQUEST_LOG_TABLE].spec().fields] == ["dt"]

    ts = datetime.now(UTC)
    log = tables[gc.EMBEDDING_INFERENCE_LOG_TABLE]
    log.upsert(_arrow(log, [{
        "subject_type": "photo", "subject_key": SHA, "task": "image_embedding",
        "version": "gemini-embedding-2:768/exif-rgb-max1024-jpeg90", "input_hash": SHA, "status": "BLANK",
        "attempts": 1, "tag_count": None, "last_error": None, "gold_run_id": "r1", "gold_updated_at": ts,
        "detail": '{"gray_std": 1.2, "blank_std": 4.0}',
    }]), join_cols=list(gc.INFERENCE_LOG_KEYS))
    assert log.scan().to_arrow().to_pylist()[0]["detail"].startswith('{"gray_std"')
    assert catalog.load_table(env.table(gc.INFERENCE_LOG_TABLE)).scan().to_arrow().num_rows == 0


def test_group_embedding_does_not_touch_existing_tagging_tables(world):
    """태깅이 먼저 만든 test 표가 있어도 --group embedding 은 그 표를 바꾸지 않는다."""
    catalog, env = world
    gc.create_tables(env, catalog, group="tagging")
    before = {b: catalog.load_table(env.table(b)).metadata_location for b in gc.GROUPS["tagging"]}
    changed = gc.create_tables(env, catalog, group="embedding")
    assert sorted(changed) == sorted(f"만듦 {env.table(b)}" for b in gc.GROUPS["embedding"])
    assert {b: catalog.load_table(env.table(b)).metadata_location for b in gc.GROUPS["tagging"]} == before


def test_prod_and_test_locations():
    test, prod = gc.get_gold_environment("test"), gc.get_gold_environment("prod")
    assert test.table(gc.IMAGE_EMBEDDING_TABLE) == "rebom_gold.gold_image_embedding_test"
    assert test.location(gc.IMAGE_EMBEDDING_TABLE).endswith("/gold/_test/iceberg/gold_image_embedding_test")
    assert prod.table(gc.IMAGE_EMBEDDING_TABLE) == "rebom_gold.gold_image_embedding"
    assert prod.location(gc.IMAGE_EMBEDDING_TABLE).endswith("/gold/iceberg/gold_image_embedding")
