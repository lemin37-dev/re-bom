"""Gemini 임베딩 래퍼 테스트. 실제 API 는 부르지 않고 가짜 client 를 쓴다."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from rebom.embedding import gemini as g


class FakeModels:
    def __init__(self, fail_times: int = 0, code: int = 429):
        self.calls: list = []
        self.fail_times = fail_times
        self.code = code

    def embed_content(self, model, contents, config):
        self.calls.append((model, contents, config))
        if self.fail_times:
            self.fail_times -= 1
            err = RuntimeError("rate limited")
            err.code = self.code
            raise err
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[float(i), 1.0]) for i, _ in enumerate(contents)])


def make(models: FakeModels) -> g.GeminiEmbedder:
    return g.GeminiEmbedder(client=SimpleNamespace(models=models), sleep=lambda s: None)


def test_prefixes():
    assert g.query_text("흰색 소형견") == "task: search result | query: 흰색 소형견"
    assert g.document_text("순함", title="말티즈") == "title: 말티즈 | text: 순함"
    assert g.document_text("순함") == "title: none | text: 순함"


@pytest.mark.parametrize(
    "head,mime",
    [(b"\xff\xd8\xff\xe0", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"),
     (b"RIFF\x00\x00\x00\x00WEBP", "image/webp"), (b"GIF89a", "image/gif")],
)
def test_sniff_mime(head, mime):
    assert g.sniff_mime(head + b"\x00" * 8) == mime


def test_sniff_mime_rejects_html():
    with pytest.raises(ValueError):
        g.sniff_mime(b"<html>404</html>")


def test_embed_texts_batches_and_keeps_order(monkeypatch):
    monkeypatch.setattr(g, "TEXT_BATCH", 2)
    models = FakeModels()
    vecs = make(models).embed_texts(["a", "b", "c"])
    assert len(models.calls) == 2
    assert [c[0] for c in models.calls] == [g.MODEL, g.MODEL]
    assert vecs == [[0.0, 1.0], [1.0, 1.0], [0.0, 1.0]]


def test_retries_on_rate_limit():
    models = FakeModels(fail_times=2)
    emb = make(models)
    assert emb.embed_texts(["a"]) == [[0.0, 1.0]]
    assert len(models.calls) == 3
    assert len(emb.latencies) == 1


def test_no_retry_on_bad_request():
    models = FakeModels(fail_times=1, code=400)
    with pytest.raises(RuntimeError):
        make(models).embed_texts(["a"])
    assert len(models.calls) == 1


def test_missing_key(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(g, "REPO_ROOT", tmp_path)
    with pytest.raises(RuntimeError):
        g.load_api_key()
