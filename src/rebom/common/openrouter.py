"""OpenRouter Chat Completions 호출 (역할 공용: 사진 태깅, 나중에 임베딩·Jev).

키는 역할마다 다른 환경변수 이름으로 받는다(예: 사진 태깅 OPENROUTER_KEY_TAG_IMG). 환경변수에 없으면
레포 루트 .env 를 읽는다(읽기만). 키 값은 로그·예외 메시지에 넣지 않는다.

공급자는 고정한다(provider.order + allow_fallbacks false + require_parameters true). 다른 공급자로
몰래 넘어가 결과가 바뀌는 일을 막기 위해서다. 응답마다 요청 모델·응답 모델·공급자·토큰·비용(usage.cost)을
ChatResult 로 돌려주므로, 호출하는 쪽이 요청 기록(gold_model_request_log)에 그대로 남긴다.

media_resolution: OpenRouter 문서에 없는 통과 파라미터다. 요청 본문 맨 위에 "MEDIA_RESOLUTION_MEDIUM" 을 넣으면
Google 공급자(AI Studio·Vertex)가 이미지를 medium 해상도로 읽는다(2026-11-20 PC 실측, 특징 질문 입력 1,294토큰).
image_url.detail 은 low 만 반영되고 medium 은 무시된다. 동작이 바뀔 수 있으니 호출하는 쪽이 prompt_tokens 를 감시한다.

키·계정 오류(401, 402, 검열 표시가 없는 403, openrouter_key_limit)는 AccountError 로 올린다. 이 오류는 사진 탓이 아니므로
호출하는 쪽이 실행 전체를 멈춰야 한다. 사진 한 장의 실패로 세면 모든 대상의 시도 횟수가 헛되이 쌓인다.
검열 표시(error.metadata 의 flagged_input·reasons)가 있는 403 은 그 입력만의 문제라 일반 OpenRouterError 다.
"""
from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

API_BASE = "https://openrouter.ai/api/v1"
APP_TITLE = "rebom"  # X-Title. OpenRouter 순위 표시용이며 키 구분과는 상관없다
RETRY_STATUS = {408, 429, 500, 502, 503, 504}
ACCOUNT_STATUS = {401, 402}
MAX_BACKOFF_SEC = 60
REPO_ROOT = Path(__file__).resolve().parents[3]


class OpenRouterError(RuntimeError):
    """재시도해도 안 되는 호출 오류(형식 오류, 4xx 등)."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class AccountError(OpenRouterError):
    """키·계정 오류(키 무효, 크레딧 부족, 키 한도 초과). 실행 전체를 멈춰야 한다."""


def _status_of(http_status: int, code: str | None) -> int | None:
    if http_status != 200:
        return http_status
    return int(code) if code and code.isdigit() else None


def _is_account_error(status: int | None, err: Any, text: str) -> bool:
    if "openrouter_key_limit" in text or status in ACCOUNT_STATUS:
        return True
    if status == 403:
        meta = err.get("metadata") if isinstance(err, dict) else None
        return not (isinstance(meta, dict) and ("flagged_input" in meta or "reasons" in meta))
    return False


def _retry_after(resp: Any) -> float | None:
    """Retry-After 헤더(초). 날짜 형식이나 없는 경우는 None."""
    value = (getattr(resp, "headers", None) or {}).get("Retry-After")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def load_key(env_name: str) -> str:
    """환경변수 → 레포 .env 순서로 키를 읽는다. 없으면 RuntimeError(키 이름만 알림)."""
    key = os.environ.get(env_name)
    if not key:
        env_file = REPO_ROOT / ".env"
        if env_file.exists():
            from dotenv import dotenv_values

            key = dotenv_values(env_file).get(env_name)
    if not key:
        raise RuntimeError(f"{env_name} 가 환경변수나 .env 에 없습니다")
    return key


def image_data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


@dataclass(frozen=True)
class ChatRequest:
    model: str
    provider: str
    system: str
    prompt: str
    schema: dict
    schema_name: str
    image: bytes | None = None
    image_mime: str = "image/jpeg"
    reasoning_effort: str | None = "low"
    media_resolution: str | None = None  # 예: "MEDIA_RESOLUTION_MEDIUM"
    temperature: float | None = None

    def body(self) -> dict:
        content: list[dict] = []
        if self.image is not None:
            content.append({"type": "image_url", "image_url": {"url": image_data_url(self.image, self.image_mime)}})
        content.append({"type": "text", "text": self.prompt})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system},
                {"role": "user", "content": content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": self.schema_name, "strict": True, "schema": self.schema},
            },
            "provider": {"order": [self.provider], "allow_fallbacks": False, "require_parameters": True},
            "usage": {"include": True},
        }
        if self.reasoning_effort:
            body["reasoning"] = {"effort": self.reasoning_effort}
        if self.media_resolution:
            body["media_resolution"] = self.media_resolution
        if self.temperature is not None:
            body["temperature"] = self.temperature
        return body


@dataclass
class ChatResult:
    content: str
    request_id: str | None
    requested_model: str
    response_model: str | None
    provider: str | None
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cost_usd: float | None
    latency_ms: int
    attempts: int
    finish_reason: str | None = None
    native_finish_reason: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    def json(self) -> Any:
        """응답 본문을 JSON 으로. 코드 블록(```json)으로 감싸 와도 벗겨 낸다."""
        text = self.content.strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        return json.loads(text)


class OpenRouterClient:
    def __init__(
        self,
        key_env: str,
        *,
        key: str | None = None,
        session: requests.Session | None = None,
        timeout: float = 120,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        api_base: str = API_BASE,
        title: str = APP_TITLE,
    ):
        self._key = key or load_key(key_env)
        self.key_env = key_env
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.sleep = sleep
        self.api_base = api_base.rstrip("/")
        self.title = title

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key}", "X-Title": self.title}

    def chat(self, req: ChatRequest) -> ChatResult:
        """한 번 호출. 429·5xx·네트워크 오류는 max_retries 번 다시 시도한다.

        기다리는 시간은 지수 백오프와 Retry-After 중 긴 쪽이며 MAX_BACKOFF_SEC 를 넘지 않는다.
        """
        body = req.body()
        last = ""
        for attempt in range(1, self.max_retries + 2):
            t0 = time.perf_counter()
            try:
                resp = self.session.post(
                    f"{self.api_base}/chat/completions", json=body, headers=self._headers(), timeout=self.timeout
                )
                data = resp.json()
            except (requests.RequestException, ValueError) as exc:
                last = f"{type(exc).__name__}: {exc}"[:300]
                if attempt <= self.max_retries:
                    self.sleep(self._backoff(attempt, None))
                    continue
                raise OpenRouterError(f"재시도 초과: {last}") from exc
            latency_ms = int((time.perf_counter() - t0) * 1000)
            err = data.get("error") if isinstance(data, dict) else None
            if resp.status_code != 200 or err:
                text = json.dumps(err or data, ensure_ascii=False)[:500]
                code = str((err or {}).get("code")) if isinstance(err, dict) else None
                status = _status_of(resp.status_code, code)
                if _is_account_error(status, err, text):
                    raise AccountError(f"키·계정 오류({self.key_env}): {text}", status=status, code=code)
                last = f"HTTP {resp.status_code}: {text}"
                if status in RETRY_STATUS and attempt <= self.max_retries:
                    self.sleep(self._backoff(attempt, _retry_after(resp)))
                    continue
                raise OpenRouterError(last, status=resp.status_code, code=code)
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            usage = data.get("usage") or {}
            return ChatResult(
                content=(message.get("content") or "").strip(),
                request_id=data.get("id"),
                requested_model=req.model,
                response_model=data.get("model"),
                provider=data.get("provider"),
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                reasoning_tokens=int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
                cost_usd=usage.get("cost"),
                latency_ms=latency_ms,
                attempts=attempt,
                finish_reason=choice.get("finish_reason"),
                native_finish_reason=choice.get("native_finish_reason"),
                raw=data,
            )
        raise OpenRouterError(f"재시도 초과: {last}")

    @staticmethod
    def _backoff(attempt: int, retry_after: float | None) -> float:
        return min(max(2 ** (attempt - 1), retry_after or 0), MAX_BACKOFF_SEC)

    def canonical_slug(self, model: str) -> str | None:
        """Models API 에서 모델의 실제 버전(canonical_slug)을 읽는다. 실패하면 None (실행은 계속)."""
        try:
            resp = self.session.get(f"{self.api_base}/models", timeout=60)
            for item in resp.json().get("data", []):
                if item.get("id") == model:
                    return item.get("canonical_slug")
        except (requests.RequestException, ValueError):
            return None
        return None
