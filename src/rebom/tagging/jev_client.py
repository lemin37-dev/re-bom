"""Jev 호출: OpenRouter Decisions API (typesafe/jev-1.14, 공급자 TypeSafe 고정). 글 태깅(run_text_tags)이 쓴다.

Chat Completions 가 아니라 Decisions API(POST /api/alpha/decisions)라 rebom.common.openrouter 의 ChatRequest 를 쓰지 않고
여기에 따로 둔다. 키 읽기(load_key)와 오류 종류(AccountError·OpenRouterError)는 공용 코드를 그대로 쓴다.

- 요청: {model, provider, state: {"설명 글": 글}, questions: {이름: {type, instructions, criteria}}}
- 응답: answers[이름] = noul 이면 {"noul": 예 확률}, choice 면 {"choice", "probabilities": {보기: 확률}}. usage.cost 포함.
- 공급자는 고정한다(order ["typesafe"], allow_fallbacks false). 다른 공급자로 넘어가 결과가 바뀌는 일을 막는다.
- 키·계정 오류(401·402·키 한도)는 AccountError 로 올린다. 글 하나의 실패가 아니므로 호출하는 쪽이 실행을 멈춘다.
- 429·5xx·524·529·네트워크 오류는 max_retries 번 다시 시도한다(지수 백오프, Retry-After 를 따름, 최대 60초).
- 키 값은 로그·예외에 넣지 않는다.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import requests

from rebom.common.openrouter import AccountError, OpenRouterError, load_key

API_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.14"
PROVIDER = {"order": ["typesafe"], "allow_fallbacks": False}
STATE_KEY = "설명 글"  # Jev 가 보는 항목 이름. 시험 때와 같은 이름을 쓴다(질문 문구가 이 이름을 전제로 함)
RETRY_STATUS = {408, 429, 500, 502, 503, 504, 524, 529}
MAX_BACKOFF_SEC = 60
APP_TITLE = "rebom"


@dataclass
class DecisionResult:
    answers: dict
    request_id: str | None
    response_model: str | None
    provider: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    latency_ms: int
    attempts: int
    raw: dict = field(default_factory=dict, repr=False)


def yes_prob(answer: dict) -> float:
    """noul 답의 예 확률."""
    if "noul" in answer:
        return float(answer["noul"])
    probs = answer.get("probabilities") or {}
    for k in ("true", "True", "yes"):
        if k in probs:
            return float(probs[k])
    raise KeyError(f"noul 확률이 없습니다: {sorted(answer)}")


class JevClient:
    def __init__(
        self,
        key_env: str,
        *,
        key: str | None = None,
        session: requests.Session | None = None,
        timeout: float = 120,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        api_url: str = API_URL,
        model: str = MODEL,
    ):
        self._key = key or load_key(key_env)
        self.key_env = key_env
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.sleep = sleep
        self.api_url = api_url
        self.model = model

    def body(self, text: str, questions: dict) -> dict:
        return {"model": self.model, "provider": PROVIDER, "state": {STATE_KEY: text}, "questions": questions}

    def decide(self, text: str, questions: dict) -> DecisionResult:
        body = self.body(text, questions)
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json", "X-Title": APP_TITLE}
        last = ""
        for attempt in range(1, self.max_retries + 2):
            t0 = time.perf_counter()
            try:
                resp = self.session.post(self.api_url, json=body, headers=headers, timeout=self.timeout)
                data = resp.json()
            except (requests.RequestException, ValueError) as exc:
                last = f"{type(exc).__name__}: {exc}"[:300]
                if attempt <= self.max_retries:
                    self.sleep(self._backoff(attempt, None))
                    continue
                raise OpenRouterError(f"재시도 초과: {last}") from exc
            latency_ms = int((time.perf_counter() - t0) * 1000)
            err = data.get("error") if isinstance(data, dict) else None
            if resp.status_code != 200 or err or not isinstance(data, dict) or "answers" not in data:
                text_ = json.dumps(err or data, ensure_ascii=False)[:500]
                code = str(err.get("code")) if isinstance(err, dict) and err.get("code") is not None else None
                status = resp.status_code if resp.status_code != 200 else (int(code) if code and code.isdigit() else None)
                if status in (401, 402) or "openrouter_key_limit" in text_:
                    raise AccountError(f"키·계정 오류({self.key_env}): {text_}", status=status, code=code)
                last = f"HTTP {resp.status_code}: {text_}"
                if status in RETRY_STATUS and attempt <= self.max_retries:
                    self.sleep(self._backoff(attempt, _retry_after(resp)))
                    continue
                raise OpenRouterError(last, status=resp.status_code, code=code)
            usage = data.get("usage") or {}
            return DecisionResult(
                answers=data["answers"],
                request_id=data.get("id"),
                response_model=data.get("model"),
                provider=data.get("provider"),
                input_tokens=int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
                cost_usd=usage.get("cost"),
                latency_ms=latency_ms,
                attempts=attempt,
                raw=data,
            )
        raise OpenRouterError(f"재시도 초과: {last}")

    @staticmethod
    def _backoff(attempt: int, retry_after: float | None) -> float:
        return min(max(2 ** (attempt - 1), retry_after or 0), MAX_BACKOFF_SEC)


def _retry_after(resp: Any) -> float | None:
    value = (getattr(resp, "headers", None) or {}).get("Retry-After")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
