"""common/openrouter.py: 요청 본문(공급자 고정·medium 해상도), 재시도, 키·계정 오류. 실제 호출 없이 가짜 세션으로 본다."""
import json
from pathlib import Path

import pytest

from rebom.common.openrouter import (
    AccountError,
    ChatRequest,
    OpenRouterClient,
    OpenRouterError,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "tagging"


class FakeResponse:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return self.responses.pop(0)


def request():
    return ChatRequest(
        model="google/gemini-3.1-flash-lite", provider="google-ai-studio", system="sys", prompt="질문",
        schema={"type": "object"}, schema_name="trait", image=b"\xff\xd8\xffabc", image_mime="image/jpeg",
        reasoning_effort="low", media_resolution="MEDIA_RESOLUTION_MEDIUM", temperature=1.0,
    )


def fixture(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def client(session, slept=None, **kw):
    sleep = slept.append if slept is not None else (lambda s: None)
    return OpenRouterClient("OPENROUTER_KEY_TEST", key="test-key", session=session, sleep=sleep, **kw)


def test_body_pins_provider_and_sends_medium_resolution():
    body = request().body()
    assert body["provider"] == {"order": ["google-ai-studio"], "allow_fallbacks": False, "require_parameters": True}
    assert body["media_resolution"] == "MEDIA_RESOLUTION_MEDIUM"
    assert body["reasoning"] == {"effort": "low"}
    assert body["temperature"] == 1.0 and "seed" not in body
    assert body["response_format"]["json_schema"]["strict"] is True
    content = body["messages"][1]["content"]
    assert content[0]["type"] == "image_url" and content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert content[1] == {"type": "text", "text": "질문"}
    assert "detail" not in content[0]["image_url"]


def test_chat_returns_usage_cost_provider():
    s = FakeSession([FakeResponse(200, fixture("openrouter_trait_response.json"))])
    res = client(s).chat(request())
    assert res.prompt_tokens == 1294 and res.reasoning_tokens == 40
    assert res.cost_usd == pytest.approx(0.000697)
    assert res.provider == "Google AI Studio"
    assert res.response_model == "google/gemini-3.1-flash-lite-20260507"
    assert res.json()["fur"]["value"] == "매끈"
    assert (res.finish_reason, res.native_finish_reason) == ("stop", "STOP")
    assert s.posts[0]["headers"]["Authorization"] == "Bearer test-key"


def test_retries_429_then_succeeds():
    s = FakeSession([
        FakeResponse(429, {"error": {"code": 429, "message": "rate"}}),
        FakeResponse(200, fixture("openrouter_trait_response.json")),
    ])
    res = client(s).chat(request())
    assert res.attempts == 2 and len(s.posts) == 2


def test_retry_after_header_sets_the_wait():
    s = FakeSession([
        FakeResponse(429, {"error": {"code": 429, "message": "rate"}}, headers={"Retry-After": "7"}),
        FakeResponse(429, {"error": {"code": 429, "message": "rate"}}, headers={"Retry-After": "600"}),
        FakeResponse(429, {"error": {"code": 429, "message": "rate"}}),
        FakeResponse(200, fixture("openrouter_trait_response.json")),
    ])
    slept = []
    res = client(s, slept).chat(request())
    assert res.attempts == 4
    assert slept == [7, 60, 4]  # 헤더 값, 상한 60초, 헤더 없으면 지수 백오프


@pytest.mark.parametrize("status, payload", [
    (401, {"error": {"code": 401, "message": "No auth credentials found"}}),
    (402, {"error": {"code": 402, "message": "Insufficient credits"}}),
    (403, {"error": {"code": 403, "message": "Key limit exceeded"}}),
    (403, {"error": {"code": 403, "message": "openrouter_key_limit exceeded"}}),
    (200, {"error": {"code": 402, "message": "Insufficient credits"}}),
])
def test_account_errors_stop_without_retry(status, payload):
    s = FakeSession([FakeResponse(status, payload)])
    with pytest.raises(AccountError):
        client(s).chat(request())
    assert len(s.posts) == 1


def test_moderation_403_is_a_per_request_error():
    flagged = {"error": {"code": 403, "message": "flagged", "metadata": {"reasons": ["x"], "flagged_input": "..."}}}
    s = FakeSession([FakeResponse(403, flagged)])
    with pytest.raises(OpenRouterError) as exc:
        client(s).chat(request())
    assert not isinstance(exc.value, AccountError) and len(s.posts) == 1


def test_bad_request_is_not_retried():
    s = FakeSession([FakeResponse(400, {"error": {"code": 400, "message": "bad schema"}})])
    with pytest.raises(OpenRouterError) as exc:
        client(s).chat(request())
    assert exc.value.status == 400 and len(s.posts) == 1


def test_error_message_does_not_contain_key():
    s = FakeSession([FakeResponse(500, {"error": {"code": 500}})] * 4)
    with pytest.raises(OpenRouterError) as exc:
        client(s, max_retries=3).chat(request())
    assert "test-key" not in str(exc.value)


def test_missing_key_names_env_only(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_KEY_NOPE", raising=False)
    import rebom.common.openrouter as mod

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="OPENROUTER_KEY_NOPE"):
        OpenRouterClient("OPENROUTER_KEY_NOPE")
