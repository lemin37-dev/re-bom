"""옛 parquet → Gold Iceberg 임베딩 표 옮기기. S3 는 가짜, Gemini 는 FakeEmbedder, Iceberg 는 로컬 SQLite 카탈로그."""
from __future__ import annotations

import json
from array import array

import pytest

from rebom.embedding import cli, gemini, gold_migrate as gm, manifest as mf, prep
from tests.test_embedding_pipeline import FakeEmbedder, make_world

MODEL, DIM = gemini.MODEL, 768


def staged_world(monkeypatch):
    s3 = make_world()
    monkeypatch.setenv("REBOM_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(cli, "_s3", lambda: s3)
    monkeypatch.setattr(gemini, "GeminiEmbedder", FakeEmbedder)
    FakeEmbedder.calls = []
    assert cli.main(["embed", "--limit", "10"]) == 0
    return s3


def row(sha, source, run, vec):
    return {"desertion_no": "1", "image_slot": 1, "photo_version": 1, "sha256": sha, "s3_key": "k", "photo_run_id": "p",
            "embedding": array("f", vec), "source": source, "embed_run_id": run, "prep_version": prep.PREP_VERSION,
            "embedded_at": "2026-11-18T01:00:33Z"}


def test_build_one_row_per_sha_prefers_latest_api_row():
    runs = [
        gm.Run("r1", [row("a", "api", "r1", [1, 0]), row("a", "dup", "r1", [1, 0]), row("b", "api", "r1", [0, 1])],
               {"skipped_sha256": {"z": "blank"}, "started_at": "2026-11-17T06:54:13Z"}),
        gm.Run("r2", [row("a", "reuse", "r2", [1, 0]), row("b", "api", "r2", [0.6, 0.8])], {}),
    ]
    plan = gm.build(runs, MODEL, 2, prep.PREP_VERSION, blank_scores={"z": 1.23456})
    assert set(plan.image) == {"a", "b"}
    assert plan.image["a"]["gold_run_id"] == "r1"  # 재사용(r2)이 아니라 처음 부른 실행
    assert plan.image["b"]["gold_run_id"] == "r2" and list(plan.image["b"]["embedding"]) == pytest.approx([0.6, 0.8])
    a = plan.image["a"]
    assert (a["subject_type"], a["subject_key"], a["source_hash"], a["model"], a["prompt_version"]) == (
        "photo", "a", "a", f"{MODEL}:2", prep.PREP_VERSION)
    assert plan.stats["copies_with_different_vector"] == 1  # b 를 다시 불러 값이 달라짐
    by = {r["subject_key"]: r for r in plan.logs}
    assert by["a"]["status"] == "OK" and by["a"]["version"] == f"{MODEL}:2/{prep.PREP_VERSION}"
    assert by["z"]["status"] == "BLANK" and json.loads(by["z"]["detail"]) == {"gray_std": 1.2346, "blank_std": 4.0}
    assert plan.stats["log_ok"] == 2 and plan.stats["log_blank"] == 1 and plan.stats["blank_without_score"] == 0


def test_canary_row_uses_session_id_or_checked_at():
    doc = {"checked_at": "2026-11-19T07:05:20Z", "ok": True, "threshold": 0.99, "min_cosine": 1.0,
           "cosines": {"s1": 1.0, "s2": 0.999}, "created_reference": True, "reference_created_at": "2026-11-19T07:05:16Z",
           "api_usage": {"via": "openrouter", "calls": 1}}
    r = gm.canary_row("checks/date=2026-11-19/070520.json", doc, "m:768")
    assert r["gold_run_id"] == "canary-20261119T070520" and r["passed"] is True and r["photo_count"] == 2
    doc["api_usage"]["session_id"] = "canary-20261120T180927-1a51d6"
    assert gm.canary_row("k", doc, "m:768")["gold_run_id"] == "canary-20261120T180927-1a51d6"


def test_plan_from_real_pipeline_output_and_blank_score(monkeypatch):
    s3 = staged_world(monkeypatch)
    runs = gm.read_runs(s3, "test-bucket", MODEL, DIM)
    current = mf.latest_photos(mf.load_manifests(s3, "test-bucket"))
    scores = gm.measure_blanks(s3, "test-bucket", runs, current)
    plan = gm.build(runs, MODEL, DIM, prep.PREP_VERSION, (), scores)
    shas = {p.sha256 for p in current.values()}
    blank = set(scores)
    assert len(blank) == 1 and scores[next(iter(blank))] < prep.BLANK_STD
    assert set(plan.image) == shas - blank  # 사진당 1행, 빈 사진 제외
    assert plan.stats["image_rows_without_api_row"] == 0 and plan.stats["copies_with_different_vector"] == 0
    assert plan.stats["log_blank"] == 1 and plan.stats["blank_without_score"] == 0


def test_cli_default_is_plan_only(monkeypatch, capsys):
    staged_world(monkeypatch)
    monkeypatch.setattr("rebom.storage.silver.aws_catalog.get_aws_catalog",
                        lambda **k: pytest.fail("계획만 출력할 때는 카탈로그에 붙지 않는다"), raising=False)
    assert cli.main(["gold-migrate", "--gold-env", "test"]) == 0
    out = capsys.readouterr().out
    assert "gold-migrate plan" in out and '"image_rows"' in out


def test_write_and_verify_idempotent_and_refuses_foreign_rows(monkeypatch, tmp_path):
    pytest.importorskip("pyiceberg")
    pytest.importorskip("sqlalchemy")
    from pyiceberg.catalog.sql import SqlCatalog

    from rebom.common import gold_catalog as gc

    s3 = staged_world(monkeypatch)
    runs = gm.read_runs(s3, "test-bucket", MODEL, DIM)
    current = mf.latest_photos(mf.load_manifests(s3, "test-bucket"))
    doc = {"checked_at": "2026-11-19T07:05:20Z", "ok": True, "threshold": 0.99, "min_cosine": 1.0, "cosines": {"x": 1.0}}
    plan = gm.build(runs, MODEL, DIM, prep.PREP_VERSION, [("k", doc)], gm.measure_blanks(s3, "test-bucket", runs, current))

    catalog = SqlCatalog("t", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    env = gc.GoldEnvironment("unit", "rebom_gold", "_unit", f"file://{tmp_path}/wh/gold")
    gc.create_tables(env, catalog)
    # 같은 임베딩 처리 기록 표의 다른 작업(글 임베딩) 행은 건드리지 않는다
    log_t = catalog.load_table(env.table(gc.EMBEDDING_INFERENCE_LOG_TABLE))
    text_row = {"subject_type": "animal", "subject_key": "1", "task": "text_embedding", "version": "v", "input_hash": "t",
                "status": "OK", "gold_run_id": "text-run"}
    log_t.append(gm._table_arrow(log_t, [text_row]))

    assert gm.write(catalog, env, plan) == {"image": len(plan.image), "log": len(plan.logs), "canary": 1}
    first = gm.verify(catalog, env, plan)
    assert first["ok"], first
    assert first["log_by_status"] == {"BLANK": 1, "OK": len(plan.image)}
    gm.write(catalog, env, plan)  # 다시 해도 같은 결과
    assert gm.verify(catalog, env, plan)["ok"]
    tasks = catalog.load_table(env.table(gc.EMBEDDING_INFERENCE_LOG_TABLE)).scan().to_arrow().column("task").to_pylist()
    assert tasks.count("text_embedding") == 1
    assert catalog.load_table(env.table(gc.INFERENCE_LOG_TABLE)).scan().to_arrow().num_rows == 0  # 태깅 표에는 안 씀

    # 전환 뒤 DAG 가 쓴 행이 있으면 지우지 않고 멈춘다
    image_t = catalog.load_table(env.table(gc.IMAGE_EMBEDDING_TABLE))
    extra = dict(next(iter(plan.image.values())), subject_key="new", gold_run_id="20261122T030000-dagdag")
    image_t.append(gm._table_arrow(image_t, [extra], vector=True))
    with pytest.raises(gm.ForeignRowsError):
        gm.write(catalog, env, plan)
