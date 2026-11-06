"""임베딩 제로샷 태깅: 사진 벡터와 보기별 문장 벡터의 유사도로 보기를 고른다.

임베딩 모델(EmbeddingGemma 2 등)은 무거워서 여기서는 불러오지 않는다. 벡터 계산은
scripts/tagging/predict_embedding_zeroshot.py 가 하고, 여기는 문장 목록과 확률 계산만 둔다.
유사도는 확률이 아니어서 softmax(유사도/온도)로 바꾼다. 온도는 1위 정확도에는 영향이 없고
기준 확률별 채택률에만 영향을 준다.
"""
from __future__ import annotations

import math

from .photo_tags import DOG_BREEDS, PALETTE

# 태그 → 보기 → 비교 문장들(평균 벡터를 씀). '알 수 없음'은 임베딩에 없어서 기준 확률로 거른다.
COLOR_WORDS = {
    "흰색": "흰색", "검정": "검은색", "회색": "회색", "갈색": "갈색",
    "크림·황토": "크림색 또는 연한 황토색", "주황": "주황색(치즈색)",
}
assert set(COLOR_WORDS) == set(PALETTE)

PROMPTS: dict[str, dict[str, list[str]]] = {
    "color_main": {
        c: [f"{w} 털을 가진 개 사진", f"{w} 털을 가진 고양이 사진", f"털 색이 대부분 {w}인 동물"]
        for c, w in COLOR_WORDS.items()
    },
    "injury": {
        "있음": ["상처나 붕대가 보이는 다친 동물 사진", "넥카라를 쓰거나 다리에 붕대를 감은 동물", "피가 나거나 상처가 있는 동물"],
        "없음": ["다친 곳 없이 건강해 보이는 동물 사진", "상처가 없는 깨끗한 동물 사진"],
    },
    "photo_quality": {
        "잘 보임": ["동물이 선명하게 잘 보이는 사진", "동물 전체가 또렷하게 찍힌 사진"],
        "가려짐·흐림": ["철창이나 케이지 너머로 찍힌 동물 사진", "흐리거나 멀리서 찍혀 잘 안 보이는 동물 사진"],
        "동물 없음": ["동물이 없는 사진", "빈 우리나 바닥만 찍힌 사진"],
    },
    "species": {
        "개": ["개 사진", "강아지 사진"],
        "고양이": ["고양이 사진", "새끼 고양이 사진"],
        "기타": ["토끼, 새, 거북이 같은 개나 고양이가 아닌 동물 사진"],
    },
    "breed": {b: [f"{b} 사진", f"{b}를 닮은 개"] for b in DOG_BREEDS},
}


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def mean_vector(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    return [sum(col) / n for col in zip(*vectors)]


def softmax_probs(sims: dict[str, float], temperature: float) -> dict[str, float]:
    m = max(sims.values())
    exps = {k: math.exp((v - m) / temperature) for k, v in sims.items()}
    total = sum(exps.values())
    return {k: v / total for k, v in exps.items()}


def zero_shot(image_vec: list[float], option_vecs: dict[str, list[float]], temperature: float = 0.02) -> dict[str, float]:
    """사진 벡터 1개와 보기별(평균) 문장 벡터 → 보기별 확률."""
    sims = {k: cosine(image_vec, v) for k, v in option_vecs.items()}
    return softmax_probs(sims, temperature)
