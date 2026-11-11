"""Jev 질문지 만들기와 응답 읽기.

응답 모양은 Jev Playground 의 "View details" 원본(JSON)을 기준으로 했다(노션 "Jev Playground 한국어 정확도 테스트").
  answers.qN = {"type": "choice", "choice": ..., "probabilities": {보기: 확률}}
             | {"type": "boolean", "probability": p}
             | {"type": "score", "score": x, "probabilities": {...}}
API 요청 형식(엔드포인트·이미지 전달 방식)은 아직 확인 전이라 call_jev 는 비워 뒀다.
키는 .env 의 JEV_API_KEY 에서만 읽는다.
"""
from __future__ import annotations

from .photo_tags import CHOICE_TAGS, DOG_BREEDS, UNKNOWN, ChoiceTag

MAX_QUESTIONS = 5  # 플레이그라운드 기준 state 하나에 질문 최대 5개

BREED_TAG = ChoiceTag("breed", "사진 속 개와 외형이 가장 닮은 품종은 무엇입니까?", (*DOG_BREEDS, UNKNOWN))


def question_batches(tags: tuple[ChoiceTag, ...] = CHOICE_TAGS, *, with_breed: bool = False) -> list[list[dict]]:
    """질문지를 MAX_QUESTIONS 개씩 나눈다. 각 질문은 {"id", "tag", "type", "question", "choices"}."""
    all_tags = list(tags) + ([BREED_TAG] if with_breed else [])
    qs = [
        {"tag": t.key, "type": "choice", "question": t.question, "choices": list(t.choices)}
        for t in all_tags
    ]
    batches = [qs[i : i + MAX_QUESTIONS] for i in range(0, len(qs), MAX_QUESTIONS)]
    for batch in batches:
        for n, q in enumerate(batch, start=1):
            q["id"] = f"q{n}"
    return batches


def parse_answers(response: dict, batch: list[dict]) -> dict[str, dict[str, float]]:
    """Jev 응답 → {태그: {보기: 확률}}. boolean 은 {"예": p, "아니오": 1-p} 로 바꾼다."""
    answers = response.get("answers", {})
    out: dict[str, dict[str, float]] = {}
    for q in batch:
        a = answers.get(q["id"])
        if not a:
            continue
        if a.get("type") == "boolean":
            p = float(a["probability"])
            out[q["tag"]] = {"예": p, "아니오": 1 - p}
        else:
            out[q["tag"]] = {k: float(v) for k, v in a.get("probabilities", {}).items()}
    return out


def call_jev(image_path: str, batch: list[dict], *, state_text: str | None = None) -> dict:
    """사진 한 장 + 질문 묶음으로 Jev 를 부른다. 요청 형식 확인 후 채운다."""
    raise NotImplementedError(
        "Jev 이미지 입력 API 형식(엔드포인트, 사진을 URL로 주는지 업로드하는지)을 확인한 뒤 구현한다. "
        "그 전에는 플레이그라운드 결과를 parse_answers 형식의 JSONL로 저장해 score 스크립트에 넣으면 된다."
    )
