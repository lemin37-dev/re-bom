"""canary.py: 매일 embed 전 모델 변화 감시. 가짜 S3·가짜 임베더만 쓴다 (실제 API·AWS 호출 없음)."""
from __future__ import annotations

import hashlib
import io
import json
import random

import pytest

from rebom.embedding import canary, cli, gemini, prep, staging

DIM = 4
MODEL = gemini.MODEL


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def get_paginator(self, name):
        objs = self.objects

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k} for k in sorted(objs) if k.startswith(Prefix)]}
        return P()

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = Body


def noise_png(seed: int) -> bytes:
    from PIL import Image

    rng = random.Random(seed)
    img = Image.new("RGB", (32, 32))
    img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(32 * 32)])
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def unit(i: int) -> list[float]:
    v = [0.0] * DIM
    v[i % DIM] = 1.0
    v[(i + 1) % DIM] = 0.5
    return prep.l2_normalize(v)


def seeded_s3(n: int = 8) -> tuple[FakeS3, dict[bytes, list[float]]]:
    """Bronze 사진 n장 + 그 벡터가 들어 있는 S3 결과 parquet. jpeg → 기준 벡터 표도 돌려준다."""
    s3 = FakeS3()
    rows, table = [], {}
    for i in range(n):
        data = noise_png(i)
        sha = hashlib.sha256(data).hexdigest()
        key = f"bronze/rescue/images/{i}.png"
        s3.objects[key] = data
        vec = unit(i)
        table[prep.to_jpeg(data)] = vec
        rows.append({"desertion_no": str(i), "image_slot": 1, "photo_version": 1, "sha256": sha, "s3_key": key,
                     "photo_run_id": "r", "embedding": vec, "source": "api", "embed_run_id": "run1",
                     "prep_version": prep.PREP_VERSION, "embedded_at": "t"})
    rows.append({**rows[0], "desertion_no": "dup", "source": "dup"})  # dup 은 기준 후보가 아님
    s3.objects[staging.run_prefix(MODEL, DIM, "run1") + "part-00000.parquet"] = staging.to_parquet(rows, DIM)
    return s3, table


class FakeEmbedder:
    def __init__(self, table, drift: bool = False):
        self.table, self.drift, self.calls = table, drift, 0

    def embed_images(self, images):
        self.calls += 1
        out = [list(self.table[j]) for j in images]
        if self.drift:  # 첫 사진 벡터만 크게 바뀜
            out[0] = [-x for x in out[0]]
        return out

    def usage(self):
        return {"via": "fake", "calls": self.calls}


def test_first_run_builds_reference_and_passes():
    s3, table = seeded_s3()
    emb = FakeEmbedder(table)
    res = canary.run(s3, "b", emb, MODEL, DIM)
    assert res.ok and res.created_reference and emb.calls == 1  # 요청 1번
    assert len(res.cosines) == canary.CANARY_SIZE and res.min_cosine == pytest.approx(1.0)
    ref = canary.load_reference(s3, "b", MODEL, DIM)
    assert [p["sha256"] for p in ref["photos"]] == sorted(p["sha256"] for p in ref["photos"])  # sha256 순, 고정
    assert all(k.startswith(canary.ROOT) for k in s3.objects if "canary" in k)
    assert res.drift_key is None and json.loads(s3.objects[res.check_key])["ok"] is True

    res2 = canary.run(s3, "b", FakeEmbedder(table), MODEL, DIM)  # 두 번째는 기준을 그대로 쓴다
    assert res2.ok and not res2.created_reference


def test_canary_files_do_not_leak_into_staged():
    """기준 JPEG·json 이 gold/image_embedding/ 아래에 있으면 load_staged 가 읽어 버린다."""
    s3, table = seeded_s3()
    canary.run(s3, "b", FakeEmbedder(table), MODEL, DIM)
    assert not canary.ROOT.startswith(staging.ROOT)
    st = staging.load_staged(s3, "b", MODEL, DIM, prep.PREP_VERSION)
    assert st.runs == ["run1"]


def test_drift_stops_saves_vectors_and_explains():
    s3, table = seeded_s3()
    canary.run(s3, "b", FakeEmbedder(table), MODEL, DIM)
    res = canary.run(s3, "b", FakeEmbedder(table, drift=True), MODEL, DIM)
    assert not res.ok and res.min_cosine < canary.THRESHOLD
    assert res.drift_key.startswith(canary.prefix(MODEL, DIM) + "drift/date=")
    drift = json.loads(s3.objects[res.drift_key])
    assert drift["model"] == MODEL and len(drift["vectors"]) == canary.CANARY_SIZE
    msg = canary.drift_message(res, "b", MODEL, DIM)
    assert "벡터값이 변경되었습니다" in msg and "모델 버전 확인이 필요합니다" in msg
    assert res.drift_key in msg and "대응 방법" in msg


def test_threshold_boundary():
    s3, table = seeded_s3()
    canary.run(s3, "b", FakeEmbedder(table), MODEL, DIM)

    class Slight(FakeEmbedder):
        def embed_images(self, images):
            out = super().embed_images(images)
            out[0] = [x + 0.05 for x in out[0]]  # 코사인 약 0.99x
            return out

    res = canary.run(s3, "b", Slight(table), MODEL, DIM)
    assert 0.99 <= res.min_cosine < 1.0 and res.ok


def test_reference_needs_enough_photos():
    s3, table = seeded_s3(n=3)
    with pytest.raises(RuntimeError):
        canary.run(s3, "b", FakeEmbedder(table), MODEL, DIM)


def test_cli_exit_codes_and_reset(monkeypatch, capsys):
    s3, table = seeded_s3()
    monkeypatch.setenv("REBOM_S3_BUCKET", "b")
    monkeypatch.setattr(cli, "_s3", lambda: s3)
    monkeypatch.setattr(gemini, "GeminiEmbedder", lambda dim: FakeEmbedder(table))
    assert cli.main(["canary", "--dim", str(DIM)]) == 0

    monkeypatch.setattr(gemini, "GeminiEmbedder", lambda dim: FakeEmbedder(table, drift=True))
    assert cli.main(["canary", "--dim", str(DIM)]) == canary.EXIT_DRIFT
    assert "모델 버전 확인이 필요합니다" in capsys.readouterr().out

    monkeypatch.setattr(gemini, "GeminiEmbedder", lambda dim: FakeEmbedder(table))
    assert cli.main(["canary", "--reset", "--dim", str(DIM)]) == 0
    assert any("/reference_archive/" in k for k in s3.objects)  # 이전 기준은 지우지 않고 보관
