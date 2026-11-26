"""Gemini Embedding 2 API 로 텍스트·사진 벡터를 만든다.

키는 환경 변수 GEMINI_API_KEY (또는 레포 루트 .env) 에서 읽는다. 무료 등급은 입력이 Google 학습에 쓰이므로
유료 등급 키를 쓴다. 보호자 실명(careOwnerNm)·연락처는 입력에 넣지 않는다.

- 모델: gemini-embedding-2. 텍스트와 사진이 같은 벡터 공간에 들어간다.
- 차원: 기본 3072 이지만 pgvector HNSW 인덱스가 2,000 차원까지라 768 을 기본으로 쓴다(잘린 차원도 API 가 정규화).
- 접두어: task_type 파라미터가 없어서 문장 앞에 붙인다. 질의는 "task: search result | query: …",
  문서(요약 카드)는 "title: … | text: …". 사진은 접두어 없이 넣는다.
- 11-18 결정: 사진과 글 모두 이 모델 하나로 만든다 (EG2 는 쓰지 않음).
- 11-19: 서버 운영 키는 OpenRouter 키(sk-or-…)다. 키가 sk-or- 로 시작하면 OpenRouter
  (google/gemini-embedding-2, 제공자 Google)로 보내고, 아니면 Google SDK 로 직접 보낸다.
  사진 12장·글로 실측: Google 직접으로 만든 S3 벡터와 코사인 1.000000 (같은 전처리·768차원), 사진 1장 $0.000116.

설치: pip install -U google-genai python-dotenv (OpenRouter 경로는 표준 라이브러리만 씀)
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

MODEL = "gemini-embedding-2"
DEFAULT_DIM = 768
FULL_DIM = 3072  # output_dimensionality 를 주지 않을 때의 차원
TEXT_BATCH = 100  # 한 번 호출에 넣는 문장 수
IMAGE_BATCH = 6  # 한 번 호출에 넣는 사진 수 (로컬 벤치마크와 같음)
RETRY_CODES = {408, 429, 500, 502, 503, 504}

OPENROUTER_URL = "https://openrouter.ai/api/v1/embeddings"
OPENROUTER_MODEL = f"google/{MODEL}"
OPENROUTER_KEY_PREFIX = "sk-or-"
# 비용 구분용 표시 (11-19). OpenRouter 의 provider 이름(Google AI Studio 등)은 실제 처리한 곳이라 바꿀 수 없어서,
# 요청마다 누가·어디서(user → 생성 기록 external_user)와 어느 실행(session_id)을 붙인다.
# 앱 이름 헤더는 공개 순위에 나오지 않게 hidden. 키·개인정보는 넣지 않는다.
APP_NAME = "rebom-embed"
APP_HEADERS = {
    "HTTP-Referer": "https://github.com/lemin37-dev/re-bom",
    "X-OpenRouter-Title": APP_NAME,
    "X-Title": APP_NAME,
    "X-OpenRouter-App-Visibility": "hidden",
}
USAGE_TAG_ENV = "REBOM_USAGE_TAG"  # 예: rebom-embed:airflow:embed_photos (DAG task 마다 지정)
DEFAULT_USAGE_TAG = f"{APP_NAME}:local"  # 내 PC 등 지정하지 않은 실행


def usage_tag() -> str:
    """요청에 붙일 '누가·어디서' 표시. 256자 이내, 공백 없이."""
    tag = (os.environ.get(USAGE_TAG_ENV) or DEFAULT_USAGE_TAG).strip().replace(" ", "_")
    return tag[:256]

REPO_ROOT = Path(__file__).resolve().parents[3]


def query_text(text: str) -> str:
    """검색 질의·제로샷 보기 문장용 접두어. 앞뒤 공백은 지운다 (handoff_20261120/text_card_reference.py 와 같음)."""
    return f"task: search result | query: {text.strip()}"


def document_text(text: str, title: str | None = None) -> str:
    """요약 카드 같은 문서용 접두어."""
    return f"title: {title or 'none'} | text: {text}"


def sniff_mime(data: bytes) -> str:
    """파일 내용으로 이미지 형식을 판별한다. 공고 사진은 확장자와 실제 형식이 다른 경우가 있다."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    raise ValueError("JPEG/PNG/WEBP/GIF 가 아닌 파일")


# 서버 운영용 임베딩 전용 키 (11-19: 모델 비용 실측을 위해 다른 용도와 키를 나눔). 노드 역할은 rebom/* 만 읽는다
DEFAULT_SECRET_ID = "rebom/embed/prod/gemini_api_key"
# 시크릿 값이 JSON 이면 이 이름들 중 처음 있는 필드를 쓴다 (handoff_20261120/tools/photo_embed.py 와 같은 순서)
SECRET_FIELDS = ("GEMINI_API_KEY", "gemini_api_key", "api_key", "key")


def _key_from_secrets_manager() -> str | None:
    """서버 Pod 용. REBOM_GEMINI_SECRET_ID(기본 rebom/embed/prod/gemini_api_key)를 노드 역할·AWS 프로필로 읽는다."""
    secret_id = os.environ.get("REBOM_GEMINI_SECRET_ID", DEFAULT_SECRET_ID)
    try:
        import boto3
    except ImportError:
        return None
    region = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
    try:
        raw = boto3.client("secretsmanager", region_name=region).get_secret_value(SecretId=secret_id)["SecretString"]
    except Exception as exc:  # noqa: BLE001 - 자격증명 만료·권한 없음 등을 한 메시지로
        raise RuntimeError(f"Secrets Manager {secret_id} 를 읽지 못했습니다: {type(exc).__name__}") from exc
    try:
        import json

        data = json.loads(raw)
    except ValueError:  # JSON 이 아니면 값 자체가 키
        return raw.strip()
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, dict):
        found = next((str(data[f]).strip() for f in SECRET_FIELDS if data.get(f)), None)
        if found is None and len(data) == 1:  # 필드가 하나뿐이면 이름과 상관없이 그 값 (예: {"rebom-embed-prod": ...})
            found = str(next(iter(data.values())) or "").strip() or None
        return found
    return None


def load_api_key() -> str:
    """GEMINI_API_KEY 환경 변수 → 레포 루트 .env → Secrets Manager 순서로 찾는다."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        try:
            from dotenv import dotenv_values
        except ImportError:  # python-dotenv 가 없으면 환경 변수만 본다
            dotenv_values = None
        if dotenv_values is not None:
            key = dotenv_values(REPO_ROOT / ".env").get("GEMINI_API_KEY")
    if not key and os.environ.get("REBOM_GEMINI_SECRET_ID", DEFAULT_SECRET_ID):
        key = _key_from_secrets_manager()
    if not key:
        raise RuntimeError("GEMINI_API_KEY 가 없습니다. .env 또는 Secrets Manager(REBOM_GEMINI_SECRET_ID)를 확인해 주세요.")
    return key


def _error_code(exc: Exception) -> int | None:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


class OpenRouterError(RuntimeError):
    """OpenRouter HTTP 오류. code 로 재시도 여부를 정한다(GeminiEmbedder._call)."""

    def __init__(self, code: int | None, message: str):
        super().__init__(f"OpenRouter {code}: {message}")
        self.code = code


@dataclass
class _Values:
    values: list[float]


@dataclass
class _Result:
    embeddings: list[_Values]
    meta: dict[str, Any] = field(default_factory=dict)  # 요청 1번의 기록 (Gold 요청 기록 표용, OpenRouter 만)


class _OpenRouterModels:
    """google-genai 의 client.models.embed_content 와 같은 모양으로 OpenRouter 를 부른다.

    contents 는 문자열(글) 또는 {"content": [{"type": "image_url", ...}]}(사진) 목록. 입력 하나 → 벡터 하나.
    """

    def __init__(self, api_key: str, timeout: float, post: Callable[[dict], dict] | None = None,
                 user: str | None = None, session_id: str | None = None):
        self._key = api_key
        self.timeout = timeout
        self._post = post or self._http_post
        self.user = user  # 누가·어디서 (OpenRouter 생성 기록의 external_user)
        self.session_id = session_id  # 어느 실행 (생성 기록의 session_id)
        self.cost = 0.0  # OpenRouter usage.cost 합 (크레딧 = USD)
        self.providers: dict[str, int] = {}
        self.served_models: dict[str, int] = {}

    def _http_post(self, body: dict) -> dict:
        req = urllib.request.Request(
            OPENROUTER_URL, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json", **APP_HEADERS},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", errors="replace")
            raise OpenRouterError(exc.code, detail) from None
        except (TimeoutError, urllib.error.URLError) as exc:  # 대기 초과·연결 실패는 다시 시도
            raise OpenRouterError(408, type(exc).__name__) from None

    def embed_content(self, model: str, contents: Sequence[Any], config: Any) -> _Result:
        dim = config["output_dimensionality"] if isinstance(config, dict) else config.output_dimensionality
        body = {"model": OPENROUTER_MODEL if model == MODEL else model, "input": list(contents),
                "dimensions": dim, "encoding_format": "float"}
        if self.user:
            body["user"] = self.user
        if self.session_id:
            body["session_id"] = self.session_id
        out = self._post(body)
        if out.get("error"):  # 200 인데 본문에 오류가 오는 경우
            err = out["error"] if isinstance(out["error"], dict) else {"message": out["error"]}
            code = err.get("code")
            raise OpenRouterError(code if isinstance(code, int) else None, str(err.get("message")))
        data = sorted(out.get("data") or [], key=lambda d: d["index"])
        usage = out.get("usage") or {}
        self.cost += float(usage.get("cost") or 0)
        prov = out.get("provider") or "?"
        self.providers[prov] = self.providers.get(prov, 0) + 1
        served = out.get("model") or "?"  # 응답이 알려 주는 실제 모델 이름 (버전 추적용)
        self.served_models[served] = self.served_models.get(served, 0) + 1
        meta = {"request_id": out.get("id"), "requested_model": body["model"], "response_model": out.get("model"),
                "provider": out.get("provider"), "prompt_tokens": usage.get("prompt_tokens"),
                "cost_usd": float(usage["cost"]) if usage.get("cost") is not None else None}
        return _Result([_Values(list(d["embedding"])) for d in data], meta)


class OpenRouterClient:
    """genai.Client 대신 쓰는 얇은 OpenRouter 클라이언트 (models.embed_content 만)."""

    def __init__(self, api_key: str, timeout: float = 60.0, post: Callable[[dict], dict] | None = None,
                 user: str | None = None, session_id: str | None = None):
        self.models = _OpenRouterModels(api_key, timeout, post, user, session_id)

    def __repr__(self) -> str:  # 키가 로그에 찍히지 않게
        return f"OpenRouterClient(timeout={self.models.timeout})"


def is_openrouter_key(key: str) -> bool:
    return key.startswith(OPENROUTER_KEY_PREFIX)


def _text_contents(texts: Sequence[str]) -> list[Any]:
    """문장마다 Content 하나. 문자열 목록을 그대로 넘기면 gemini-embedding-2 는 한 입력의 여러 부분으로 보고
    벡터 1개만 돌려준다(2026-11-11 실측)."""
    try:
        from google.genai import types
    except ImportError:  # 테스트에서 SDK 없이 쓸 때
        return list(texts)
    return [types.Content(parts=[types.Part.from_text(text=t)]) for t in texts]


@dataclass
class GeminiEmbedder:
    """embed_content 호출을 감싼다. client 를 넘기면(테스트용) 그대로 쓴다.

    키가 OpenRouter 키면 OpenRouterClient, 아니면 google-genai Client. timeout 은 OpenRouter 요청 1번의 대기 초
    (검색 API 질의는 5초 + 재요청 2번: GeminiEmbedder(timeout=5, max_retries=2)).
    """

    dim: int = DEFAULT_DIM
    client: Any = None
    max_retries: int = 5
    sleep: Callable[[float], None] = time.sleep
    timeout: float = 60.0
    session_id: str | None = None  # OpenRouter 비용 구분: 어느 실행인지 (예: embed run_id)
    latencies: list[float] = field(default_factory=list)  # 호출별 초

    def __post_init__(self) -> None:
        if self.client is None:
            key = load_api_key()
            if is_openrouter_key(key):
                self.client = OpenRouterClient(key, timeout=self.timeout, user=usage_tag(), session_id=self.session_id)
            else:
                from google import genai

                self.client = genai.Client(api_key=key)

    @property
    def via_openrouter(self) -> bool:
        return isinstance(self.client, OpenRouterClient)

    def usage(self) -> dict[str, Any]:
        """요약(summary.json)용: 경로·호출 수·OpenRouter 비용."""
        out: dict[str, Any] = {"via": "openrouter" if self.via_openrouter else "google", "calls": len(self.latencies)}
        if self.via_openrouter:
            m = self.client.models
            out.update(cost_usd=round(m.cost, 6), providers=dict(m.providers), served_models=dict(m.served_models),
                       usage_tag=m.user, session_id=m.session_id)
        return out

    def _texts(self, texts: Sequence[str]) -> list[Any]:
        return list(texts) if self.via_openrouter else _text_contents(texts)

    def _image(self, data: bytes) -> Any:
        if self.via_openrouter:
            url = f"data:{sniff_mime(data)};base64," + base64.b64encode(data).decode("ascii")
            return {"content": [{"type": "image_url", "image_url": {"url": url}}]}
        from google.genai import types

        return types.Content(parts=[types.Part.from_bytes(data=data, mime_type=sniff_mime(data))])

    def _config(self) -> Any:
        try:
            from google.genai import types
        except ImportError:  # 테스트에서 SDK 없이 쓸 때
            return {"output_dimensionality": self.dim}
        return types.EmbedContentConfig(output_dimensionality=self.dim)

    def _call(self, contents: Any) -> list[list[float]]:
        return self._call_recorded(contents)[0]

    def _call_recorded(self, contents: Any) -> tuple[list[list[float]], dict[str, Any]]:
        """벡터와 요청 기록(요청 id·제공자·토큰·비용·지연·시도 수·요청 시각). Google 직접 경로는 앞 4개가 없다."""
        requested_at = datetime.now(timezone.utc)
        for attempt in range(self.max_retries + 1):
            t0 = time.perf_counter()
            try:
                result = self.client.models.embed_content(model=MODEL, contents=contents, config=self._config())
            except Exception as exc:  # noqa: BLE001 - SDK 예외 종류가 버전마다 달라 코드로 판단
                if _error_code(exc) in RETRY_CODES and attempt < self.max_retries:
                    self.sleep(min(2 ** attempt, 30))
                    continue
                raise
            took = time.perf_counter() - t0
            self.latencies.append(took)
            meta = {"requested_model": MODEL, **(getattr(result, "meta", None) or {}),
                    "latency_ms": round(took * 1000), "attempts": attempt + 1, "requested_at": requested_at}
            return [list(e.values) for e in result.embeddings], meta
        raise RuntimeError("unreachable")

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """문장마다 벡터 1개. 접두어는 호출하는 쪽에서 붙인다(query_text, document_text)."""
        out: list[list[float]] = []
        for i in range(0, len(texts), TEXT_BATCH):
            out.extend(self._call(self._texts(texts[i : i + TEXT_BATCH])))
        if len(out) != len(texts):
            raise RuntimeError(f"벡터 수가 다름: 문장 {len(texts)}, 벡터 {len(out)}")
        return out

    def embed_image(self, data: bytes) -> list[float]:
        """사진 1장 → 벡터 1개."""
        return self._call([self._image(data)])[0]

    def embed_texts_recorded(self, texts: Sequence[str]) -> tuple[list[list[float]], dict[str, Any]]:
        """Embed one batch of prepared texts and return its request metadata."""
        if not 0 < len(texts) <= TEXT_BATCH:
            raise ValueError(f"한 번에 문장 1~{TEXT_BATCH}건")
        return self._call_recorded(self._texts(texts))

    def embed_images(self, images: Sequence[bytes]) -> list[list[float]]:
        """사진 여러 장(최대 IMAGE_BATCH)을 요청 1번에. 사진마다 Content 1개라 벡터도 사진 수만큼 온다."""
        return self.embed_images_recorded(images)[0]

    def embed_images_recorded(self, images: Sequence[bytes]) -> tuple[list[list[float]], dict[str, Any]]:
        """embed_images 와 같고, 요청 기록(_call_recorded)을 함께 돌려준다. 여러 스레드가 같이 불러도 섞이지 않는다."""
        if len(images) > IMAGE_BATCH:
            raise ValueError(f"한 번에 {IMAGE_BATCH}장까지")
        out, meta = self._call_recorded([self._image(d) for d in images])
        if len(out) != len(images):
            raise RuntimeError(f"벡터 수가 다름: 사진 {len(images)}, 벡터 {len(out)}")
        return out, meta
