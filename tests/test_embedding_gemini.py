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
    monkeypatch.setenv("REBOM_GEMINI_SECRET_ID", "")  # Secrets Manager 조회도 끈다 (실제 AWS 호출 방지)
    monkeypatch.setattr(g, "REPO_ROOT", tmp_path)
    with pytest.raises(RuntimeError):
        g.load_api_key()


# --- OpenRouter 경로 (11-19, 운영 키) ------------------------------------------------


class FakeOpenRouter:
    """OpenRouter /embeddings 응답을 흉내 낸다. 받은 요청 본문을 남긴다."""

    def __init__(self, fail=None):
        self.bodies: list[dict] = []
        self.fail = list(fail or [])  # 순서대로 던질 예외

    def __call__(self, body):
        self.bodies.append(body)
        if self.fail:
            raise self.fail.pop(0)
        n = len(body["input"])
        data = [{"index": i, "embedding": [float(i), 1.0]} for i in reversed(range(n))]  # 순서가 섞여 와도 index 로 정렬
        return {"data": data, "provider": "Google", "usage": {"cost": 0.0001 * n}}


def make_or(post, **kw) -> g.GeminiEmbedder:
    return g.GeminiEmbedder(client=g.OpenRouterClient("sk-or-v1-test", post=post), sleep=lambda s: None, **kw)


def test_openrouter_key_detection():
    assert g.is_openrouter_key("sk-or-v1-abc")
    assert not g.is_openrouter_key("AIzaSyabc")


def test_openrouter_key_selects_openrouter_client(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "sk-or-v1-test")
    emb = g.GeminiEmbedder(timeout=5)
    assert emb.via_openrouter and emb.client.models.timeout == 5
    assert "sk-or" not in repr(emb.client)  # 키가 로그에 안 찍힘


def test_openrouter_images_request_shape():
    post = FakeOpenRouter()
    emb = make_or(post)
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 8
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    vecs = emb.embed_images([jpeg, png])
    body = post.bodies[0]
    assert body["model"] == "google/gemini-embedding-2" and body["dimensions"] == 768
    assert body["encoding_format"] == "float"
    urls = [x["content"][0]["image_url"]["url"] for x in body["input"]]
    assert urls[0].startswith("data:image/jpeg;base64,") and urls[1].startswith("data:image/png;base64,")
    assert vecs == [[0.0, 1.0], [1.0, 1.0]]  # index 순서
    u = emb.usage()
    assert u["via"] == "openrouter" and u["cost_usd"] == 0.0002 and u["providers"] == {"Google": 1}


def test_openrouter_texts_are_plain_strings(monkeypatch):
    monkeypatch.setattr(g, "TEXT_BATCH", 2)
    post = FakeOpenRouter()
    vecs = make_or(post).embed_texts([g.query_text(" 흰색 "), "b", "c"])
    assert [b["input"] for b in post.bodies] == [["task: search result | query: 흰색", "b"], ["c"]]
    assert len(vecs) == 3


def test_openrouter_retries_timeouts_then_gives_up():
    """검색 API 규칙: 5초 대기 + 재요청 2번 → 3번 다 실패하면 예외(호출하는 쪽이 SQL 필터 결과로 돌림)."""
    post = FakeOpenRouter(fail=[g.OpenRouterError(408, "timeout")] * 3)
    with pytest.raises(g.OpenRouterError):
        make_or(post, max_retries=2, timeout=5).embed_texts(["a"])
    assert len(post.bodies) == 3

    post = FakeOpenRouter(fail=[g.OpenRouterError(429, "rate")])
    assert make_or(post, max_retries=2).embed_texts(["a"]) == [[0.0, 1.0]]
    assert len(post.bodies) == 2


def test_openrouter_bad_key_not_retried():
    post = FakeOpenRouter(fail=[g.OpenRouterError(401, "bad key")])
    with pytest.raises(g.OpenRouterError):
        make_or(post).embed_texts(["a"])
    assert len(post.bodies) == 1


def test_openrouter_error_in_200_body():
    emb = make_or(lambda body: {"error": {"code": 400, "message": "bad input"}})
    with pytest.raises(g.OpenRouterError) as e:
        emb.embed_texts(["a"])
    assert e.value.code == 400
