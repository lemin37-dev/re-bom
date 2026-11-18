"""사진 임베딩 파이프라인 테스트. S3·RDS·Gemini 는 부르지 않고 가짜 객체를 쓴다.

픽스처(tests/fixtures/embedding/manifest_run*.json)의 sha256 은 아래 PHOTOS 바이트로 만든 값이다.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rebom.embedding import cli, gemini, manifest as mf, plan, prep, staging, store

FIXTURES = Path(__file__).parent / "fixtures" / "embedding"
PHOTOS = {
    "bronze/rescue/images/411300202500001/popfile1.jpg": b"\xff\xd8\xff photo-a1",
    "bronze/rescue/images/411300202500001/popfile2.png": b"\x89PNG\r\n\x1a\n photo-a2",
    "bronze/rescue/images/411300202500002/popfile1.jpg": b"\xff\xd8\xff photo-b1",
    "bronze/rescue/images/411300202500001/popfile1_v002.jpg": b"\xff\xd8\xff photo-a1-v2",
}


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def manifests() -> list[dict]:
    return [load("manifest_run1.json"), load("manifest_run2.json")]


# --- manifest -------------------------------------------------------------------

def test_parse_manifest_fields(manifests):
    photos = mf.parse_manifest(manifests[0])
    assert [(p.desertion_no, p.slot, p.version, p.ext) for p in photos] == [
        ("411300202500001", 1, 1, "jpg"), ("411300202500001", 2, 1, "png"), ("411300202500002", 1, 1, "jpg"),
    ]
    assert all(p.photo_run_id == "20261111T140304-aaaaaa" for p in photos)


def test_latest_photos_prefers_higher_version_and_ignores_failed(manifests):
    current = mf.latest_photos(reversed(manifests))  # 입력 순서와 상관없이 실행 시각 순으로 판단
    assert set(current) == {("411300202500001", 1), ("411300202500001", 2), ("411300202500002", 1)}
    a1 = current[("411300202500001", 1)]
    assert (a1.version, a1.s3_key, a1.photo_run_id) == (
        2, "bronze/rescue/images/411300202500001/popfile1_v002.jpg", "20261117T030123-bbbbbb")
    # 교체 실패(replaced_not_found)는 이전 버전 유지
    assert current[("411300202500002", 1)].version == 1


def test_sha256_matches_fixture_bytes(manifests):
    for p in mf.latest_photos(manifests).values():
        assert hashlib.sha256(PHOTOS[p.s3_key]).hexdigest() == p.sha256


@pytest.mark.parametrize("patch,msg", [
    ({"sha256": "xyz"}, "sha256"),
    ({"slot": 9}, "슬롯"),
    ({"original": "images/411300202500001/1.webp"}, "원본 경로"),
    ({"version": 0}, "버전"),
])
def test_parse_manifest_rejects_bad_items(manifests, patch, msg):
    doc = manifests[0]
    doc["files"][0].update(patch)
    with pytest.raises(ValueError, match=msg):
        mf.parse_manifest(doc)


def test_summarize(manifests):
    s = mf.summarize(manifests)
    assert s["current_photos"] == 3 and s["notices"] == 2
    assert s["versions"] == {2: 1, 1: 2}
    assert s["failed_reasons"] == {"not_found": 1, "replaced_not_found": 1}
    assert "sha256" in s["item_fields"] and "desertionNo" in s["item_fields"]


# --- store ----------------------------------------------------------------------

def test_vector_literal():
    assert store.to_vector_literal([1, 0.5, -2]) == "[1.0,0.5,-2.0]"


def test_pending_skips_same_hash_and_redoes_changed(manifests):
    current = mf.latest_photos(manifests)
    a2 = current[("411300202500001", 2)]
    done = {a2.key: a2.sha256, ("411300202500001", 1): "0" * 64}  # 슬롯1 은 옛 해시
    todo = store.pending(current.values(), done)
    assert [p.key for p in todo] == [("411300202500001", 1), ("411300202500002", 1)]


# --- Gemini 키 -------------------------------------------------------------------

def test_load_api_key_falls_back_to_secrets_manager(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("REBOM_GEMINI_SECRET_ID", raising=False)
    monkeypatch.setattr(gemini, "REPO_ROOT", Path("/nonexistent"))
    seen = {}

    class FakeSM:
        def get_secret_value(self, SecretId):
            seen["id"] = SecretId
            return {"SecretString": json.dumps({"gemini_api_key": "test-key"})}

    monkeypatch.setitem(__import__("sys").modules, "boto3",
                        SimpleNamespace(client=lambda name, region_name=None: FakeSM()))
    assert gemini.load_api_key() == "test-key"
    assert seen["id"] == "rebom/dev/gemini_api_key"


def test_env_key_wins_over_secret(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    assert gemini.load_api_key() == "env-key"


# --- embed 명령 (가짜 S3·Gemini, 실제 작은 JPEG) ----------------------------------------

class FakeS3:
    def __init__(self, docs, photos):
        self.objects = {f"bronze/rescue/image_runs/run{i}/manifest.json": json.dumps(d).encode() for i, d in enumerate(docs)}
        self.objects.update(photos)

    def get_paginator(self, name):
        objs = self.objects

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k, "Size": len(v)} for k, v in sorted(objs.items()) if k.startswith(Prefix)]}
        return P()

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = Body


class FakeConn:
    def __init__(self):
        self.written = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def jpeg(seed: int | None, size=(1600, 1200)) -> bytes:
    """seed 가 있으면 무늬 사진, None 이면 단색(빈 사진)."""
    from PIL import Image

    img = Image.new("RGB", (64, 48), (128, 128, 128))
    if seed is not None:  # 작은 무늬를 키워서 줄여도 무늬가 남게 한다
        img.putdata([((x * 7 + seed * 31) % 256, (x * 13) % 256, (x * 3 + seed) % 256) for x in range(64 * 48)])
    img = img.resize(size)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=95)
    return out.getvalue()


def make_world():
    """공고 3개, 칸 6개. A1·A2 와 C1 은 같은 파일(중복), B2 는 빈 사진, A1 은 run2 에서 v2 로 교체."""
    a1_old, a1, b1, blank = jpeg(1, (64, 48)), jpeg(2), jpeg(3, (64, 48)), jpeg(None, (64, 48))
    imgs = "bronze/rescue/images/"
    photos = {
        f"{imgs}411300202500001/popfile1.jpg": a1_old,
        f"{imgs}411300202500001/popfile1_v002.jpg": a1,
        f"{imgs}411300202500001/popfile2.jpg": a1,
        f"{imgs}411300202500002/popfile1.jpg": b1,
        f"{imgs}411300202500002/popfile2.jpg": blank,
        f"{imgs}411300202500003/popfile1.jpg": a1,
    }

    def item(no, slot, key, version=1):
        data = photos[f"{imgs}{key}"]
        return {"desertionNo": no, "slot": slot, "original": f"{imgs}{key}", "ext": "jpg", "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "version": version}

    run1 = {"run_id": "20261111T000000-aaaaaa", "generated_at": "2026-11-11T00:00:00Z", "files": [
        item("411300202500001", 1, "411300202500001/popfile1.jpg"),
        item("411300202500001", 2, "411300202500001/popfile2.jpg"),
        item("411300202500002", 1, "411300202500002/popfile1.jpg"),
        item("411300202500002", 2, "411300202500002/popfile2.jpg"),
    ], "failed": []}
    run2 = {"run_id": "20261117T000000-bbbbbb", "generated_at": "2026-11-17T00:00:00Z", "files": [
        item("411300202500001", 1, "411300202500001/popfile1_v002.jpg", version=2),
        item("411300202500002", 1, "411300202500002/popfile1.jpg"),  # 다시 나온 같은 항목
        item("411300202500003", 1, "411300202500003/popfile1.jpg"),
    ], "failed": []}
    return FakeS3([run1, run2], photos)


class FakeEmbedder:
    calls: list = []

    def __init__(self, dim):
        self.dim = dim

    def embed_images(self, images):
        FakeEmbedder.calls.append(len(images))
        for d in images:  # 전처리 결과 확인: JPEG, 긴 변 1024 이하
            from PIL import Image

            img = Image.open(io.BytesIO(d))
            assert img.format == "JPEG" and max(img.size) <= 1024
        return [[3.0, 4.0] + [0.0] * (self.dim - 2) for _ in images]  # 길이 5 → 정규화되어야 함


def run_cli(monkeypatch, s3, argv):
    monkeypatch.setenv("REBOM_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(cli, "_s3", lambda: s3)
    monkeypatch.setattr(gemini, "GeminiEmbedder", FakeEmbedder)
    FakeEmbedder.calls = []
    assert cli.main(argv) == 0


def staged_rows(s3):
    return [r for k, v in sorted(s3.objects.items()) if k.startswith("gold/") and k.endswith(".parquet")
            for r in staging.read_parquet(v)]


def test_plan_counts_before_sending(monkeypatch):
    s3 = make_world()
    p = plan.build(mf.load_manifests(s3, "b"), staging.Staged())
    assert p.stats["manifest_file_entries"] == 7
    assert p.stats["repeated_in_manifests"] == 1  # run2 의 B1
    assert p.stats["old_versions_dropped"] == 1  # A1 v1
    assert p.stats["current_photos"] == 5
    assert p.stats["unique_sha256"] == 3 and p.stats["duplicate_photos"] == 2
    assert p.stats["new_keys_by_run"] == {"20261111T000000-aaaaaa": 4, "20261117T000000-bbbbbb": 1}
    assert p.stats["to_send_sha256"] == 3


def test_embed_dedupes_skips_blank_and_writes_s3_not_rds(monkeypatch):
    s3 = make_world()
    monkeypatch.setattr(store, "connect", lambda: pytest.fail("embed 는 RDS 에 쓰지 않는다"))
    run_cli(monkeypatch, s3, ["embed", "--limit", "10", "--workers", "2"])
    assert FakeEmbedder.calls == [2]  # 고유 3개 중 빈 사진 1개 제외 → 요청 1번에 2장
    rows = staged_rows(s3)
    assert sorted((r["desertion_no"], r["image_slot"], r["source"]) for r in rows) == [
        ("411300202500001", 1, "api"), ("411300202500001", 2, "dup"),
        ("411300202500002", 1, "api"), ("411300202500003", 1, "dup"),
    ]
    a1 = next(r for r in rows if (r["desertion_no"], r["image_slot"]) == ("411300202500001", 1))
    assert a1["s3_key"].endswith("popfile1_v002.jpg") and a1["photo_version"] == 2
    assert all(len(r["embedding"]) == 768 for r in rows)
    assert a1["embedding"][:2] == pytest.approx([0.6, 0.8])  # L2 정규화
    summary = json.loads(next(v for k, v in s3.objects.items() if k.endswith("summary.json")))
    assert list(summary["skipped_sha256"].values()) == ["blank"]
    assert summary["stats"]["api_sha256"] == 2 and summary["status"] == "ok"


def test_second_run_sends_nothing_and_reuses(monkeypatch, capsys):
    s3 = make_world()
    run_cli(monkeypatch, s3, ["embed", "--limit", "10"])
    capsys.readouterr()
    run_cli(monkeypatch, s3, ["embed", "--limit", "10", "--dry-run"])
    out = capsys.readouterr().out
    assert '"to_send_sha256": 0' in out and '"skip_known_blank": 1' in out
    assert FakeEmbedder.calls == []


def test_limit_counts_unique_sha256(monkeypatch):
    s3 = make_world()
    run_cli(monkeypatch, s3, ["embed", "--limit", "1"])
    assert sum(FakeEmbedder.calls) <= 1


def test_embed_skips_sha_mismatch(monkeypatch):
    s3 = make_world()
    s3.objects["bronze/rescue/images/411300202500002/popfile1.jpg"] = jpeg(9, (64, 48))
    run_cli(monkeypatch, s3, ["embed", "--limit", "10"])
    assert ("411300202500002", 1) not in {(r["desertion_no"], r["image_slot"]) for r in staged_rows(s3)}
    summary = json.loads(next(v for k, v in s3.objects.items() if k.endswith("summary.json")))
    assert summary["sha_mismatch_keys"] == ["bronze/rescue/images/411300202500002/popfile1.jpg"]
    assert summary["skipped_sha256"] == {} or "blank" in summary["skipped_sha256"].values()


def test_dry_run_writes_nothing(monkeypatch):
    s3 = make_world()
    before = set(s3.objects)
    run_cli(monkeypatch, s3, ["embed", "--limit", "10", "--dry-run"])
    assert set(s3.objects) == before and FakeEmbedder.calls == []


def run_load(monkeypatch, s3, in_rds, argv=("load",)):
    conn = FakeConn()
    monkeypatch.setattr(store, "connect", lambda: conn)
    monkeypatch.setattr(store, "table_exists", lambda c: True)
    monkeypatch.setattr(store, "existing_hashes", lambda c, model: dict(in_rds))
    monkeypatch.setattr(store, "upsert", lambda c, model, rows: conn.written.extend(rows) or len(rows))
    run_cli(monkeypatch, s3, list(argv))
    return conn


def test_load_first_time_writes_all_current(monkeypatch):
    s3 = make_world()
    run_cli(monkeypatch, s3, ["embed", "--limit", "10"])
    conn = run_load(monkeypatch, s3, {})
    assert len(conn.written) == 4 and all(len(v) == 768 for _, v in conn.written)


def test_load_writes_only_changes(monkeypatch, capsys):
    s3 = make_world()
    run_cli(monkeypatch, s3, ["embed", "--limit", "10"])
    current = mf.latest_photos(mf.load_manifests(s3, "b"))
    in_rds = {k: p.sha256 for k, p in current.items() if k != ("411300202500002", 2)}  # B2 는 빈 사진이라 없음
    in_rds[("411300202500001", 1)] = "0" * 64  # 사진이 바뀐 칸 (RDS 는 옛 sha256)
    del in_rds[("411300202500003", 1)]  # 새 칸
    in_rds[("411300202599999", 1)] = "f" * 64  # manifest 에 없는 칸
    capsys.readouterr()
    conn = run_load(monkeypatch, s3, in_rds)
    assert sorted(p.key for p, _ in conn.written) == [("411300202500001", 1), ("411300202500003", 1)]
    out = capsys.readouterr().out
    assert '"unchanged_skip": 2' in out and '"new": 1' in out and '"changed_sha256": 1' in out
    assert '"rds_not_current": 1' in out


def test_load_nothing_when_rds_up_to_date_unless_full(monkeypatch):
    s3 = make_world()
    run_cli(monkeypatch, s3, ["embed", "--limit", "10"])
    in_rds = {k: p.sha256 for k, p in mf.latest_photos(mf.load_manifests(s3, "b")).items()}
    assert run_load(monkeypatch, s3, in_rds).written == []
    assert len(run_load(monkeypatch, s3, in_rds, ["load", "--full"]).written) == 4


def test_load_dry_run_writes_nothing(monkeypatch):
    s3 = make_world()
    run_cli(monkeypatch, s3, ["embed", "--limit", "10"])
    assert run_load(monkeypatch, s3, {}, ["load", "--dry-run"]).written == []


def test_load_max_write_guard_stops_before_writing(monkeypatch):
    s3 = make_world()
    run_cli(monkeypatch, s3, ["embed", "--limit", "10"])
    conn = FakeConn()
    monkeypatch.setattr(store, "connect", lambda: conn)
    monkeypatch.setattr(store, "table_exists", lambda c: True)
    monkeypatch.setattr(store, "existing_hashes", lambda c, model: {})
    monkeypatch.setattr(store, "upsert", lambda c, model, rows: conn.written.extend(rows) or len(rows))
    assert cli.main(["load", "--max-write", "3"]) == 2 and conn.written == []  # 4행 > 상한 3
    assert cli.main(["load", "--max-write", "4"]) == 0 and len(conn.written) == 4


def test_check_writes_rejects_bad_vectors():
    p = mf.Photo("1", 1, 1, "k1", "a" * 64, "jpg", None, "r")
    assert cli.check_writes([(p, [1.0] + [0.0] * 767)], 768, 0) is None
    assert "차원" in cli.check_writes([(p, [1.0] * 10)], 768, 0)
    assert "노름" in cli.check_writes([(p, [0.5] + [0.0] * 767)], 768, 0)


def test_staged_embeddings_are_compact_float32(monkeypatch):
    from array import array

    s3 = make_world()
    run_cli(monkeypatch, s3, ["embed", "--limit", "10"])
    rows = staged_rows(s3)
    assert all(isinstance(r["embedding"], array) and r["embedding"].typecode == "f" for r in rows)


def test_prep_blank_and_resize():
    assert prep.is_blank(jpeg(None, (64, 48)))
    assert not prep.is_blank(jpeg(1, (64, 48)))
    from PIL import Image

    out = Image.open(io.BytesIO(prep.to_jpeg(jpeg(1, (2048, 1024)))))
    assert out.size == (1024, 512) and out.format == "JPEG"
    small = Image.open(io.BytesIO(prep.to_jpeg(jpeg(1, (64, 48)))))
    assert small.size == (64, 48)  # 늘리지 않음


# --- smoke 명령 ------------------------------------------------------------------

def test_parse_vector_and_percentile():
    assert store.parse_vector("[1,0.5,-2]") == [1.0, 0.5, -2.0]
    assert store.parse_vector("[]") == []
    assert cli._pct([0.1, 0.2, 0.3, 0.4, 1.0], 95) == 1.0
    assert cli._pct([0.1, 0.2, 0.3, 0.4, 1.0], 50) == 0.3


def test_smoke_reports_found_and_same(monkeypatch, capsys):
    current = mf.latest_photos([load("manifest_run1.json"), load("manifest_run2.json")])
    rows = [(p.desertion_no, p.slot, p.sha256, p.s3_key, [1.0, 0.0] + [0.0] * 766) for p in current.values()]
    conn = FakeConn()
    monkeypatch.setenv("REBOM_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(cli, "_s3", lambda: FakeS3([load("manifest_run1.json"), load("manifest_run2.json")], PHOTOS))
    monkeypatch.setattr(store, "connect", lambda: conn)
    monkeypatch.setattr(store, "stored_rows", lambda c, model, limit=None: rows[:limit] if limit else rows)
    monkeypatch.setattr(store, "nearest", lambda c, model, vec, k=1: [("other", 9, rows[calls["n"] - 1][2], 1.0)])
    calls = {"n": 0}

    class FakeEmbedder:
        def __init__(self, dim):
            self.dim = dim

        def embed_image(self, data):
            if self.dim == 768:
                calls["n"] += 1
            return [2.0, 0.0] + [0.0] * (self.dim - 2)

    monkeypatch.setattr(gemini, "GeminiEmbedder", FakeEmbedder)
    monkeypatch.setattr(prep, "to_jpeg", lambda data: data)
    assert cli.main(["smoke", "--sample", "3", "--trunc", "2"]) == 0
    out = capsys.readouterr().out
    assert '"found": "3/3"' in out
    assert '"manifest_mismatch": 0' in out
    assert '"ok(>=0.99)": true' in out and '"same(>=0.99)": true' in out
    assert '"full_dim": 3072' in out
