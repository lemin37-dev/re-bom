"""검색 질의(글·사진)를 Gold 벡터와 같은 규칙으로 임베딩한다.

- 글: gemini.query_text 접두어("task: search result | query: …"). 공고 카드는 document_text 로 만들었다(text_card.py).
- 사진: prep.to_jpeg(EXIF 회전·RGB·긴 변 1024·JPEG 90). Gold 사진 임베딩과 같은 전처리다.
- 정규화는 하지 않는다. 코사인 거리는 벡터 크기와 상관없다.
검색 API 는 요청마다 기다리므로 요청 1번 5초, 재요청 2번(gemini.GeminiEmbedder 설명)으로 부른다.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol, Sequence

from rebom.embedding import gemini, prep
from rebom.serving.repository import QueryVector, VectorSpec, VectorUnavailable

MAX_IMAGE_BYTES = 10 * 1024 * 1024


class Embedder(Protocol):
    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_image(self, data: bytes) -> list[float]: ...


class BadQueryImage(ValueError):
    """사진으로 열 수 없거나 너무 크거나 거의 빈 사진."""


def default_embedder(dim: int) -> Embedder:
    return gemini.GeminiEmbedder(dim=dim, timeout=5, max_retries=2)


class QueryEmbedder:
    """차원별 임베더를 처음 쓸 때 만든다(키 읽기를 서버 시작에서 미룸). 활성 버전의 모델이 이 코드가 부르는
    모델(gemini.MODEL)과 다르면 임베딩하지 않는다. 다른 모델의 벡터끼리 비교한 순위는 뜻이 없다."""

    def __init__(self, factory: Callable[[int], Embedder] = default_embedder):
        self._factory = factory
        self._by_dim: dict[int, Embedder] = {}

    def _get(self, spec: VectorSpec) -> Embedder:
        if spec.model != gemini.MODEL:
            raise VectorUnavailable(f"{spec.version} 의 모델 {spec.model} 은 검색 API 가 임베딩할 수 없습니다"
                                    f"(지원: {gemini.MODEL}). 질의 임베딩 코드를 먼저 맞춰야 합니다.")
        if spec.dim not in self._by_dim:
            try:
                self._by_dim[spec.dim] = self._factory(spec.dim)
            except RuntimeError as exc:  # 키 없음·Secrets Manager 권한 없음 (gemini.load_api_key)
                raise VectorUnavailable(
                    f"Gemini 키를 읽지 못했습니다({exc}). 사람에게 API 파드가 {gemini.DEFAULT_SECRET_ID}"
                    "(또는 REBOM_GEMINI_SECRET_ID)를 읽을 수 있게 해 달라고 요청해 주세요.") from None
        return self._by_dim[spec.dim]

    def text(self, spec: VectorSpec, text: str) -> QueryVector:
        return self._check(spec, self._get(spec).embed_texts([gemini.query_text(text)])[0])

    def image(self, spec: VectorSpec, data: bytes) -> QueryVector:
        if len(data) > MAX_IMAGE_BYTES:
            raise BadQueryImage("사진은 10MB 까지입니다.")
        try:
            gemini.sniff_mime(data)
            if prep.is_blank(data):
                raise BadQueryImage("거의 빈 사진이라 찾을 수 없습니다.")
            jpeg = prep.to_jpeg(data)
        except (BadQueryImage, ImportError):  # Pillow 가 없는 건 사진 탓이 아니다 (503)
            raise
        except Exception as exc:  # noqa: BLE001 - 형식 판별·Pillow 오류를 한 메시지로
            raise BadQueryImage("JPEG·PNG·WEBP·GIF 사진만 받습니다.") from exc
        return self._check(spec, self._get(spec).embed_image(jpeg))

    @staticmethod
    def _check(spec: VectorSpec, values: Sequence[Any]) -> QueryVector:
        if len(values) != spec.dim:
            raise RuntimeError(f"질의 벡터 차원 {len(values)} != {spec.dim}")
        return QueryVector(spec, tuple(float(x) for x in values))
