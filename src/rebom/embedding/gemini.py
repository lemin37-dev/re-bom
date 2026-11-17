"""Gemini Embedding 2 API 로 텍스트·사진 벡터를 만든다.

키는 환경 변수 GEMINI_API_KEY (또는 레포 루트 .env) 에서 읽는다. 무료 등급은 입력이 Google 학습에 쓰이므로
유료 등급 키를 쓴다. 보호자 실명(careOwnerNm)·연락처는 입력에 넣지 않는다.

- 모델: gemini-embedding-2. 텍스트와 사진이 같은 벡터 공간에 들어간다.
- 차원: 기본 3072 이지만 pgvector HNSW 인덱스가 2,000 차원까지라 768 을 기본으로 쓴다(잘린 차원도 API 가 정규화).
- 접두어: task_type 파라미터가 없어서 문장 앞에 붙인다. 질의는 "task: search result | query: …",
  문서(요약 카드)는 "title: … | text: …". 사진은 접두어 없이 넣는다.
- EmbeddingGemma 2 와 벡터 공간이 달라 섞어 쓸 수 없다.

설치: pip install -U google-genai python-dotenv
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

MODEL = "gemini-embedding-2"
DEFAULT_DIM = 768
FULL_DIM = 3072  # output_dimensionality 를 주지 않을 때의 차원
TEXT_BATCH = 100  # 한 번 호출에 넣는 문장 수
IMAGE_BATCH = 6  # 한 번 호출에 넣는 사진 수 (로컬 벤치마크와 같음)
RETRY_CODES = {429, 500, 502, 503, 504}

REPO_ROOT = Path(__file__).resolve().parents[3]


def query_text(text: str) -> str:
    """검색 질의·제로샷 보기 문장용 접두어."""
    return f"task: search result | query: {text}"


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


DEFAULT_SECRET_ID = "rebom/dev/gemini_api_key"  # JSON {"gemini_api_key": "..."}. 노드 역할은 rebom/* 만 읽는다
SECRET_FIELD = "gemini_api_key"


def _key_from_secrets_manager() -> str | None:
    """서버 Pod 용. REBOM_GEMINI_SECRET_ID(기본 rebom/dev/gemini_api_key)를 노드 역할·AWS 프로필로 읽는다."""
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

        return json.loads(raw).get(SECRET_FIELD)
    except ValueError:  # JSON 이 아니면 값 자체가 키
        return raw.strip()


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
    """embed_content 호출을 감싼다. client 를 넘기면(테스트용) 그대로 쓴다."""

    dim: int = DEFAULT_DIM
    client: Any = None
    max_retries: int = 5
    sleep: Callable[[float], None] = time.sleep
    latencies: list[float] = field(default_factory=list)  # 호출별 초

    def __post_init__(self) -> None:
        if self.client is None:
            from google import genai

            self.client = genai.Client(api_key=load_api_key())

    def _config(self) -> Any:
        try:
            from google.genai import types
        except ImportError:  # 테스트에서 SDK 없이 쓸 때
            return {"output_dimensionality": self.dim}
        return types.EmbedContentConfig(output_dimensionality=self.dim)

    def _call(self, contents: Any) -> list[list[float]]:
        for attempt in range(self.max_retries + 1):
            t0 = time.perf_counter()
            try:
                result = self.client.models.embed_content(model=MODEL, contents=contents, config=self._config())
            except Exception as exc:  # noqa: BLE001 - SDK 예외 종류가 버전마다 달라 코드로 판단
                if _error_code(exc) in RETRY_CODES and attempt < self.max_retries:
                    self.sleep(min(2 ** attempt, 30))
                    continue
                raise
            self.latencies.append(time.perf_counter() - t0)
            return [list(e.values) for e in result.embeddings]
        raise RuntimeError("unreachable")

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """문장마다 벡터 1개. 접두어는 호출하는 쪽에서 붙인다(query_text, document_text)."""
        out: list[list[float]] = []
        for i in range(0, len(texts), TEXT_BATCH):
            out.extend(self._call(_text_contents(texts[i : i + TEXT_BATCH])))
        if len(out) != len(texts):
            raise RuntimeError(f"벡터 수가 다름: 문장 {len(texts)}, 벡터 {len(out)}")
        return out

    def embed_image(self, data: bytes) -> list[float]:
        """사진 1장 → 벡터 1개."""
        from google.genai import types

        part = types.Part.from_bytes(data=data, mime_type=sniff_mime(data))
        return self._call([part])[0]

    def embed_images(self, images: Sequence[bytes]) -> list[list[float]]:
        """사진 여러 장(최대 IMAGE_BATCH)을 요청 1번에. 사진마다 Content 1개라 벡터도 사진 수만큼 온다."""
        from google.genai import types

        if len(images) > IMAGE_BATCH:
            raise ValueError(f"한 번에 {IMAGE_BATCH}장까지")
        contents = [types.Content(parts=[types.Part.from_bytes(data=d, mime_type=sniff_mime(d))]) for d in images]
        out = self._call(contents)
        if len(out) != len(images):
            raise RuntimeError(f"벡터 수가 다름: 사진 {len(images)}, 벡터 {len(out)}")
        return out
