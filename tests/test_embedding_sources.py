"""임베딩 입력 선택(--source bronze|silver) 테스트. S3·Glue·Gemini 는 부르지 않는다."""
from __future__ import annotations

import pytest

from rebom.embedding import cli, gemini, manifest as mf, plan, sources, staging
from tests.test_embedding_pipeline import FakeEmbedder, make_world

IMGS = mf.IMAGES_PREFIX
SHA_A = "a" * 64
SHA_B = "b" * 64


def silver_row(no, slot, key=None, sha=SHA_A):
    return {"desertion_no": no, "image_slot": slot, "original_s3_key": key, "sha256": sha}


def test_photos_from_silver_reads_version_from_key_and_skips_empty():
    rows = [
        silver_row("1", 1, f"{IMGS}1/popfile1_v002.jpg"),
        silver_row("1", 2, f"{IMGS}1/popfile2.PNG", SHA_B.upper()),
        silver_row("2", 1, None, None),  # 사진을 아직 못 받은 칸
    ]
    current = sources.photos_from_silver(rows, "test")
    assert set(current) == {("1", 1), ("1", 2)}
    a = current[("1", 1)]
    assert (a.version, a.ext, a.sha256, a.photo_run_id) == (2, "jpg", SHA_A, None)
    assert current[("1", 2)].version == 1 and current[("1", 2)].sha256 == SHA_B


@pytest.mark.parametrize("row,msg", [
    (silver_row("1", 1, f"{IMGS}1/popfile1.jpg", "xyz"), "sha256"),
    (silver_row("1", 1, f"{IMGS}2/popfile1.jpg"), "원본 경로"),  # 다른 공고 폴더
    (silver_row("1", 1, f"{IMGS}1/popfile2.jpg"), "원본 경로"),  # 칸 번호가 다름
    (silver_row("1", 9, f"{IMGS}1/popfile9.jpg"), "칸"),
])
def test_photos_from_silver_rejects_bad_rows(row, msg):
    with pytest.raises(ValueError, match=msg):
        sources.photos_from_silver([row], "test")


def test_photos_from_silver_rejects_duplicate_slot():
    row = silver_row("1", 1, f"{IMGS}1/popfile1.jpg")
    with pytest.raises(ValueError, match="두 번"):
        sources.photos_from_silver([row, dict(row)], "test")


def test_compare_finds_unapplied_replacement_and_missing():
    bronze = {
        ("1", 1): mf.Photo("1", 1, 2, f"{IMGS}1/popfile1_v002.jpg", SHA_B, "jpg", None, "r2"),
        ("1", 2): mf.Photo("1", 2, 1, f"{IMGS}1/popfile2.jpg", SHA_A, "jpg", None, "r1"),
        ("2", 1): mf.Photo("2", 1, 1, f"{IMGS}2/popfile1.jpg", SHA_A, "jpg", None, "r3"),
    }
    silver = sources.photos_from_silver([
        silver_row("1", 1, f"{IMGS}1/popfile1.jpg", SHA_A),  # 교체(v2) 반영 전
        silver_row("1", 2, f"{IMGS}1/popfile2.jpg", SHA_A),
    ], "test")
    r = sources.compare(bronze, silver)
    assert (r["same"], r["only_bronze"], r["only_silver"], r["sha_diff"], r["key_diff"]) == (1, 1, 0, 1, 1)
    assert r["diff_examples"][0]["bronze_version"] == 2 and r["diff_examples"][0]["silver_version"] == 1
    assert r["only_bronze_by_run"] == {"r3": 1} and r["switch_ready"] is False
    assert sources.compare(bronze, bronze)["switch_ready"] is True


def test_plan_same_whether_current_given_or_from_manifests():
    s3 = make_world()
    manifests = mf.load_manifests(s3, "b")
    a = plan.build(manifests, staging.Staged())
    b = plan.build(None, staging.Staged(), mf.latest_photos(manifests))
    assert a.send == b.send and a.current == b.current
    assert "manifest_file_entries" in a.stats and "manifest_file_entries" not in b.stats


def _cli(monkeypatch, s3, current):
    monkeypatch.setenv("REBOM_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(cli, "_s3", lambda: s3)
    monkeypatch.setattr(gemini, "GeminiEmbedder", FakeEmbedder)
    monkeypatch.setattr(sources, "load_silver", lambda env, profile=None: current)
    FakeEmbedder.calls = []


def test_default_source_is_bronze(monkeypatch):
    s3 = make_world()
    _cli(monkeypatch, s3, current=None)
    monkeypatch.setattr(sources, "load_silver", lambda *a, **k: pytest.fail("기본은 bronze"))
    assert cli.main(["embed", "--limit", "10", "--dry-run"]) == 0


def test_silver_test_env_blocks_writes(monkeypatch):
    s3 = make_world()
    current = mf.latest_photos(mf.load_manifests(s3, "b"))
    _cli(monkeypatch, s3, current)
    for argv in (["embed", "--limit", "10", "--source", "silver"], ["load", "--source", "silver"]):
        with pytest.raises(SystemExit, match="쓰지 않습니다"):
            cli.main(argv)
    assert FakeEmbedder.calls == []
    assert cli.main(["embed", "--limit", "10", "--dry-run", "--source", "silver"]) == 0  # 확인만은 됨


def test_silver_source_embeds_silver_photos(monkeypatch):
    s3 = make_world()
    current = mf.latest_photos(mf.load_manifests(s3, "b"))
    only_one = {k: v for k, v in current.items() if k[0] == "411300202500003"}
    _cli(monkeypatch, s3, only_one)
    assert cli.main(["embed", "--limit", "10", "--source", "silver", "--silver-env", "prod"]) == 0
    rows = [r for k, v in sorted(s3.objects.items()) if k.startswith("gold/") and k.endswith(".parquet")
            for r in staging.read_parquet(v)]
    assert {(r["desertion_no"], r["image_slot"]) for r in rows} == {("411300202500003", 1)}


def test_compare_sources_cli(monkeypatch, capsys):
    s3 = make_world()
    current = mf.latest_photos(mf.load_manifests(s3, "b"))
    _cli(monkeypatch, s3, current)
    assert cli.main(["compare-sources"]) == 0
    assert '"switch_ready": true' in capsys.readouterr().out
