"""Gold 임베딩 묶음 표 (11-20 결정): 로컬 SQLite 카탈로그 + 임시 폴더로 확인한다(AWS 없음).

- 사진 벡터는 sha256(subject_key) × 모델 × 전처리 버전당 한 행, 768차원 list<float> 그대로 읽힌다.
- --group embedding 은 임베딩 표와 공용 기록 표만 만들고 태그 표는 만들지 않는다(prod 에서 태깅 표를 건드리지 않음).
- 공용 처리 기록에 detail(빈 사진 판정 점수 등) 칸, 요청 기록에 batch_size 칸이 있다.
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


def test_group_embedding_creates_only_embedding_and_shared_tables(world):
    catalog, env = world
    made = gc.create_tables(env, catalog, group="embedding")
    assert sorted(made) == sorted(f"만듦 {env.table(b)}" for b in gc.GROUPS["embedding"])
    assert env.table(gc.TAG_TABLE) not in {".".join(t) for t in catalog.list_tables(env.database)}
    # 태깅 묶음을 나중에 만들면 태그 표만 새로 생긴다. canary 표는 대상 칸이 없어도 옛 스키마로 보지 않는다
    assert gc.create_tables(env, catalog, group="tagging") == [f"만듦 {env.table(gc.TAG_TABLE)}"]
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


def test_shared_logs_have_detail_and_batch_size(world):
    catalog, env = world
    gc.create_tables(env, catalog, group="embedding")
    inference = catalog.load_table(env.table(gc.INFERENCE_LOG_TABLE))
    requests = catalog.load_table(env.table(gc.REQUEST_LOG_TABLE))
    assert "detail" in {f.name for f in inference.schema().fields}
    assert "batch_size" in {f.name for f in requests.schema().fields}
    ts = datetime.now(UTC)
    inference.upsert(_arrow(inference, [{
        "subject_type": "photo", "subject_key": SHA, "task": "image_embedding",
        "version": "gemini-embedding-2:768/exif-rgb-max1024-jpeg90", "input_hash": SHA, "status": "BLANK",
        "attempts": 1, "tag_count": None, "last_error": None, "gold_run_id": "r1", "gold_updated_at": ts,
        "detail": '{"gray_std": 1.2, "blank_std": 4.0}',
    }]), join_cols=list(gc.INFERENCE_LOG_KEYS))
    assert inference.scan().to_arrow().to_pylist()[0]["detail"].startswith('{"gray_std"')


def test_create_adds_new_shared_columns_to_old_log_tables(world):
    """태깅이 먼저 만든 표(detail·batch_size 없음)에 --create 가 칸만 더한다."""
    catalog, env = world
    gc.create_tables(env, catalog, group="tagging")
    for base, col in ((gc.INFERENCE_LOG_TABLE, "detail"), (gc.REQUEST_LOG_TABLE, "batch_size")):
        with catalog.load_table(env.table(base)).update_schema() as update:
            update.delete_column(col)
    changed = gc.create_tables(env, catalog, group="embedding")
    assert f"칸 추가 {env.table(gc.INFERENCE_LOG_TABLE)}: detail" in changed
    assert f"칸 추가 {env.table(gc.REQUEST_LOG_TABLE)}: batch_size" in changed
    assert len([c for c in changed if c.startswith("만듦")]) == 3


def test_prod_and_test_locations():
    test, prod = gc.get_gold_environment("test"), gc.get_gold_environment("prod")
    assert test.table(gc.IMAGE_EMBEDDING_TABLE) == "rebom_gold.gold_image_embedding_test"
    assert test.location(gc.IMAGE_EMBEDDING_TABLE).endswith("/gold/_test/iceberg/gold_image_embedding_test")
    assert prod.table(gc.IMAGE_EMBEDDING_TABLE) == "rebom_gold.gold_image_embedding"
    assert prod.location(gc.IMAGE_EMBEDDING_TABLE).endswith("/gold/iceberg/gold_image_embedding")
