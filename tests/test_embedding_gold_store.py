"""매일 임베딩의 Gold 표 읽기·쓰기 (embed·load·canary --gold-env). S3·Gemini 는 가짜, Iceberg 는 로컬 SQLite 카탈로그."""
from __future__ import annotations

import hashlib
import json
from array import array
from datetime import datetime, timezone

import pytest

from rebom.embedding import canary, cli, gemini, gold_migrate as gm, gold_store as gs, manifest as mf, prep, staging
from tests.test_embedding_gemini import FakeOpenRouter, make_or
from tests.test_embedding_pipeline import FakeEmbedder, jpeg, make_world, run_load

MID = f"{gemini.MODEL}:768"
VERSION = f"{MID}/{prep.PREP_VERSION}"
AT = datetime(2026, 11, 23, 18, 0, 5, tzinfo=timezone.utc)


# ---- 요청 기록 ----------------------------------------------------------------------------------------------------

def test_recorded_call_returns_request_meta_with_retries():
    class Post(FakeOpenRouter):
        def __call__(self, body):
            out = super().__call__(body)
            return {**out, "id": "gen-1", "model": "google/gemini-embedding-2",
                    "usage": {**out["usage"], "prompt_tokens": 2580}}

    post = Post(fail=[gemini.OpenRouterError(429, "busy")])
    vecs, meta = make_or(post).embed_images_recorded([b"\xff\xd8\xff\xe0" + b"\x00" * 8] * 3)
    assert len(vecs) == 3
    assert meta["request_id"] == "gen-1" and meta["provider"] == "Google" and meta["prompt_tokens"] == 2580
    assert meta["cost_usd"] == pytest.approx(0.0003) and meta["requested_model"] == "google/gemini-embedding-2"
    assert meta["attempts"] == 2 and meta["latency_ms"] >= 0 and meta["requested_at"].tzinfo is not None


def test_request_row_is_one_row_per_request_with_full_values():
    """요청 1번 = 1행 (태깅 요청 기록과 같은 뜻). 묶어 보내도 나누지 않는다."""
    meta = {"request_id": "gen-1", "requested_model": "google/gemini-embedding-2", "provider": "Google",
            "prompt_tokens": 1001, "cost_usd": 0.0007, "latency_ms": 900, "attempts": 1, "requested_at": AT}
    one = gs.request_row(["a"], meta, prep.PREP_VERSION, "run1")
    assert (one["subject_type"], one["subject_key"], one["batch_size"]) == ("photo", "a", 1)
    assert one["cost_usd"] == 0.0007 and one["prompt_tokens"] == 1001 and one["latency_ms"] == 900
    assert one["dt"] == "2026-11-23" and one["task"] == "image_embedding" and one["status"] == "OK"
    batch = gs.request_row(["a", "b", "c", "d"], meta, prep.PREP_VERSION, "run1")  # 전체 재임베딩 때만
    assert batch["subject_key"] == "a" and batch["batch_size"] == 4 and batch["cost_usd"] == 0.0007
    assert gs.request_link(meta, 4) == {"request_id": "gen-1", "batch_size": 4}  # 나머지 사진은 처리 기록에서 이음
    none = gs.request_row(["a"], {"requested_at": AT}, prep.PREP_VERSION, "run1")  # Google 직접 경로
    assert none["cost_usd"] is None and none["prompt_tokens"] is None


# ---- 읽기 → 계획 모양 ---------------------------------------------------------------------------------------------

def test_state_staged_marks_done_skipped_and_read_errors():
    P = mf.Photo
    current = {("n1", 1): P("n1", 1, 1, "k1", "a", "jpg", 1, "r"), ("n2", 1): P("n2", 1, 1, "k2", "a", "jpg", 1, "r"),
               ("n3", 1): P("n3", 1, 1, "k3", "e", "jpg", 1, "r")}
    state = gs.State(vectors={"a": array("f", [1.0, 0.0])}, logs={
        "a": {"status": "OK", "attempts": 1, "detail": None},
        "b": {"status": "BLANK", "attempts": 1, "detail": '{"gray_std": 0.1}'},
        "c": {"status": "READ_ERROR", "attempts": 2, "detail": '{"s3_key": "bronze/x.jpg", "error": "NoSuchKey"}'},
        "d": {"status": "SHA_MISMATCH", "attempts": 1, "detail": '{"s3_key": "bronze/y.jpg"}'},
        "f": {"status": "FAILED", "attempts": 1, "detail": None},
    })
    st = state.staged(current)
    assert set(st.rows) == {("n1", 1), ("n2", 1)} and st.rows[("n1", 1)]["sha256"] == "a"
    assert st.skipped == {"b": "blank", "f": "failed"}  # 다시 보내지 않음
    assert st.read_errors == {"bronze/x.jpg": 2}  # 다시 읽고, 연속 실패 수를 이어 센다 (sha 불일치도 다시 봄)


# ---- 로컬 카탈로그로 embed → load → canary ---------------------------------------------------------------------

class RecordedEmbedder(FakeEmbedder):
    n = 0

    def embed_images_recorded(self, images):
        RecordedEmbedder.n += 1
        return self.embed_images(images), {
            "request_id": f"gen-{RecordedEmbedder.n}", "requested_model": "google/gemini-embedding-2",
            "provider": "Google", "prompt_tokens": 258 * len(images), "cost_usd": 0.000116 * len(images),
            "latency_ms": 800, "attempts": 1, "requested_at": datetime.now(timezone.utc)}


@pytest.fixture
def gold(monkeypatch, tmp_path):
    pytest.importorskip("pyiceberg")
    pytest.importorskip("sqlalchemy")
    from pyiceberg.catalog.sql import SqlCatalog

    from rebom.common import gold_catalog as gc

    catalog = SqlCatalog("t", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    env = gc.GoldEnvironment("unit", "rebom_gold", "_unit", f"file://{tmp_path}/wh/gold")
    gc.create_tables(env, catalog)
    monkeypatch.setattr(cli, "_gold_target", lambda args: (catalog, env) if getattr(args, "gold_env", None) else None)

    def table(base):
        return catalog.load_table(env.table(base)).scan().to_arrow().to_pylist()
    return catalog, env, table


def embed(monkeypatch, s3, *extra):
    monkeypatch.setenv("REBOM_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(cli, "_s3", lambda: s3)
    monkeypatch.setattr(gemini, "GeminiEmbedder", RecordedEmbedder)
    return cli.main(["embed", "--limit", "10", "--gold-env", "test", *extra])


def summaries(s3):
    return [json.loads(v) for k, v in sorted(s3.objects.items()) if k.endswith("summary.json")]


def test_embed_writes_gold_tables_and_parquet(monkeypatch, gold):
    from rebom.common import gold_catalog as gc

    _, _, table = gold
    s3 = make_world()
    FakeEmbedder.calls = []
    assert embed(monkeypatch, s3) == 0
    current = mf.latest_photos(mf.load_manifests(s3, "test-bucket"))
    blank = hashlib.sha256(s3.objects["bronze/rescue/images/411300202500002/popfile2.jpg"]).hexdigest()
    sent = {p.sha256 for p in current.values()} - {blank}

    images = table(gc.IMAGE_EMBEDDING_TABLE)
    assert {r["subject_key"] for r in images} == sent and len(images) == len(sent)  # 사진당 1행 (칸 5개, 사진 2장)
    r = images[0]
    assert (r["subject_type"], r["task"], r["model"], r["prompt_version"]) == ("photo", "image_embedding", MID,
                                                                               prep.PREP_VERSION)
    assert r["source_hash"] == r["subject_key"] and r["embedding"][:2] == pytest.approx([0.6, 0.8])

    logs = {x["subject_key"]: x for x in table(gc.EMBEDDING_INFERENCE_LOG_TABLE)}
    assert {k: v["status"] for k, v in logs.items()} == {**{s: "OK" for s in sent}, blank: "BLANK"}
    assert set(json.loads(logs[blank]["detail"])) == {"gray_std", "blank_std"} and logs[blank]["version"] == VERSION

    reqs = table(gc.EMBEDDING_REQUEST_LOG_TABLE)  # 기본 1장씩: 요청 1행 = 사진 1장
    assert sorted(x["subject_key"] for x in reqs) == sorted(sent) and {x["batch_size"] for x in reqs} == {1}
    assert len({x["request_id"] for x in reqs}) == len(sent)
    assert all(x["cost_usd"] == pytest.approx(0.000116) for x in reqs)
    by_req = {x["subject_key"]: x["request_id"] for x in reqs}
    assert all(json.loads(logs[s]["detail"]) == {"request_id": by_req[s], "batch_size": 1} for s in sent)
    assert not table(gc.INFERENCE_LOG_TABLE) and not table(gc.REQUEST_LOG_TABLE)  # 태깅 표에는 안 씀

    (summary,) = summaries(s3)
    assert summary["gold"] == {"env": "unit", "written": {"image": 2, "log": 3, "request": 2}}
    assert summary["image_batch"] == 1 and FakeEmbedder.calls == [1, 1]
    st = staging.load_staged(s3, "test-bucket", gemini.MODEL, 768, prep.PREP_VERSION)
    assert {r["sha256"] for r in st.rows.values()} == sent  # 되돌릴 때를 위한 parquet 도 같이 씀


def test_registered_embedding_versions_do_not_change_legacy_reads(monkeypatch, gold):
    from rebom.common import gold_catalog as gc
    from rebom.common.gold_version_registry import register_version
    from rebom.embedding.gold_store import read_state

    catalog, env, table = gold
    s3 = make_world()
    config = {"model_id": gemini.MODEL, "dim": 768, "preprocess_version": prep.PREP_VERSION, "blank_std": prep.BLANK_STD}
    for version in ("image_embedding@1", "image_embedding@2"):
        assert register_version(catalog, env, "image_embedding", version, config)
        assert embed(monkeypatch, s3, "--version", version) == 0

    assert not read_state(catalog, env, gemini.MODEL, 768, prep.PREP_VERSION).vectors
    assert not staging.load_staged(s3, "test-bucket", gemini.MODEL, 768, prep.PREP_VERSION).rows
    assert embed(monkeypatch, s3) == 0
    assert embed(monkeypatch, s3, "--version", "image_embedding@2") == 0
    for version in ("image_embedding@1", "image_embedding@2", None):
        assert len(read_state(catalog, env, gemini.MODEL, 768, prep.PREP_VERSION, version=version).vectors) == 2
    assert len(table(gc.IMAGE_EMBEDDING_TABLE)) == 6
    assert len(table(gc.EMBEDDING_INFERENCE_LOG_TABLE)) == 9
    assert len(table(gc.EMBEDDING_REQUEST_LOG_TABLE)) == 6
    assert {row["version"] for row in table(gc.EMBEDDING_REQUEST_LOG_TABLE)} == {
        "image_embedding@1", "image_embedding@2", None,
    }
    with pytest.raises(ValueError, match="does not match"):
        embed(monkeypatch, s3, "--version", "image_embedding@1", "--dim", "32")


def test_batched_run_is_still_one_request_row_and_links_photos(monkeypatch, gold):
    """전체 재임베딩처럼 묶어 보내도 요청 기록은 요청 1번 = 1행, 사진은 처리 기록 detail 로 이어진다."""
    from rebom.common import gold_catalog as gc

    _, _, table = gold
    s3 = make_world()
    FakeEmbedder.calls = []
    embed(monkeypatch, s3, "--image-batch", "6")
    assert FakeEmbedder.calls == [2]
    (req,) = table(gc.EMBEDDING_REQUEST_LOG_TABLE)
    assert req["batch_size"] == 2 and req["cost_usd"] == pytest.approx(0.000116 * 2)
    ok = [x for x in table(gc.EMBEDDING_INFERENCE_LOG_TABLE) if x["status"] == "OK"]
    assert len(ok) == 2 and {json.loads(x["detail"])["request_id"] for x in ok} == {req["request_id"]}
    assert req["subject_key"] in {x["subject_key"] for x in ok}


def test_next_run_reads_gold_not_parquet(monkeypatch, gold, capsys):
    s3 = make_world()
    FakeEmbedder.calls = []
    embed(monkeypatch, s3)
    for k in [k for k in s3.objects if k.startswith(staging.ROOT)]:  # parquet·summary 를 지워도
        del s3.objects[k]
    embed(monkeypatch, s3)
    assert FakeEmbedder.calls == [1, 1]  # 다시 보내지 않음 (빈 사진도)
    out = capsys.readouterr().out
    assert "원본: Gold unit 표" in out and '"skip_known_blank": 1' in out and '"to_send_sha256": 0' in out


def test_read_error_attempts_count_up_then_ok(monkeypatch, gold):
    from rebom.common import gold_catalog as gc

    _, _, table = gold
    s3 = make_world()
    key = "bronze/rescue/images/411300202500002/popfile1.jpg"
    sha = hashlib.sha256(s3.objects[key]).hexdigest()
    data = s3.objects.pop(key)
    FakeEmbedder.calls = []
    embed(monkeypatch, s3)
    embed(monkeypatch, s3)
    row = {r["subject_key"]: r for r in table(gc.EMBEDDING_INFERENCE_LOG_TABLE)}[sha]
    assert row["status"] == "READ_ERROR" and row["attempts"] == 2 and json.loads(row["detail"])["s3_key"] == key
    s3.objects[key] = data
    embed(monkeypatch, s3)
    rows = [r for r in table(gc.EMBEDDING_INFERENCE_LOG_TABLE) if r["subject_key"] == sha]
    assert [r["status"] for r in rows] == ["OK"]  # 같은 키라 바뀌어 쓰임
    assert sha in {r["subject_key"] for r in table(gc.IMAGE_EMBEDDING_TABLE)}


def test_load_reads_gold(monkeypatch, gold):
    s3 = make_world()
    FakeEmbedder.calls = []
    embed(monkeypatch, s3)
    for k in [k for k in s3.objects if k.startswith(staging.ROOT)]:
        del s3.objects[k]  # Gold 만 남김
    conn = run_load(monkeypatch, s3, {}, ["load", "--gold-env", "test"])
    keys = sorted(p.key for p, _ in conn.written)
    assert len(keys) == 4 and ("411300202500002", 2) not in keys  # 칸 5개 중 빈 사진 칸 빼고
    assert all(list(v[:2]) == pytest.approx([0.6, 0.8]) for _, v in conn.written)


def test_canary_writes_gold_row_and_requests(monkeypatch, gold):
    from rebom.common import gold_catalog as gc

    _, _, table = gold
    s3 = make_world()
    base = canary.prefix(gemini.MODEL, 768)
    vec = prep.l2_normalize([3.0, 4.0] + [0.0] * 766)
    photos = []
    for i in range(canary.CANARY_SIZE):
        data = jpeg(i + 10, (64, 48))
        sha = hashlib.sha256(data).hexdigest()
        s3.objects[f"{base}reference/{sha}.jpg"] = data
        photos.append({"sha256": sha, "s3_key": "k", "jpeg_key": f"{base}reference/{sha}.jpg", "embedding": vec})
    s3.objects[f"{base}reference/reference.json"] = json.dumps(
        {"created_at": "2026-11-19T07:05:16Z", "photos": photos}).encode()
    monkeypatch.setenv("REBOM_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(cli, "_s3", lambda: s3)
    monkeypatch.setattr(gemini, "GeminiEmbedder", RecordedEmbedder)
    assert cli.main(["canary", "--gold-env", "test"]) == 0
    (row,) = table(gc.EMBEDDING_CANARY_TABLE)
    assert row["passed"] is True and row["photo_count"] == 6 and row["model"] == MID
    reqs = table(gc.EMBEDDING_REQUEST_LOG_TABLE)  # 1장씩 요청 6번 = 6행
    assert len(reqs) == 6 and {r["gold_run_id"] for r in reqs} == {row["gold_run_id"]}
    assert {r["batch_size"] for r in reqs} == {1} and {r["subject_key"] for r in reqs} == {p["sha256"] for p in photos}
    assert len({r["request_id"] for r in reqs}) == 6


def test_gold_migrate_stops_after_direct_writes(monkeypatch, gold, capsys):
    s3 = make_world()
    FakeEmbedder.calls = []
    embed(monkeypatch, s3)
    runs = gm.read_runs(s3, "test-bucket", gemini.MODEL, 768)
    assert gm.direct_runs(runs, "unit") == [runs[0].run_id] and gm.direct_runs(runs, "prod") == []
    for r in runs:  # cli 는 --gold-env 이름으로 비교한다
        r.summary["gold"]["env"] = "test"
    monkeypatch.setattr(gm, "read_runs", lambda *a: runs)
    assert cli.main(["gold-migrate", "--gold-env", "test"]) == 2
    assert "gold-migrate 멈춤" in capsys.readouterr().err
