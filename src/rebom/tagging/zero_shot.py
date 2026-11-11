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


# 몸 특징(개만): 귀·다리·꼬리 모양. 품종 이름은 넣지 않고 모양만 묘사한다(품종 추정과 분리).
# '안 보임'은 임베딩 보기에 넣지 않는다. 정답이 '안 보임'인 사진은 채점에서 빠진다.
BODY_PROMPTS: dict[str, dict[str, list[str]]] = {
    "ear": {
        "선 귀": ["귀가 뾰족하게 위로 선 개 사진", "쫑긋 선 삼각형 귀를 가진 개", "두 귀가 곧게 서 있는 개"],
        "접힌 귀": ["귀 끝이 앞으로 접힌 개 사진", "반쯤 선 귀의 끝이 접혀 있는 개", "귀가 반만 서고 끝이 꺾인 개"],
        "늘어진 귀": ["귀가 아래로 축 늘어진 개 사진", "긴 귀가 얼굴 옆으로 처진 개", "귀가 머리 옆에 붙어 늘어진 개"],
    },
    "leg": {
        "짧은 다리": ["다리가 아주 짧은 개 사진", "몸통에 비해 다리가 짧은 개", "몸이 길고 다리가 짧은 개"],
        "보통 다리": ["다리 길이가 보통인 개 사진", "몸과 다리 비율이 평범한 개"],
        "긴 다리": ["다리가 길고 가는 개 사진", "몸에 비해 다리가 긴 날씬한 개", "키가 크고 다리가 긴 개"],
    },
    "tail": {
        "말린 꼬리": ["꼬리가 등 위로 둥글게 말린 개 사진", "말린 꼬리를 가진 개", "꼬리가 동그랗게 말려 올라간 개"],
        "곧은·처진 꼬리": ["꼬리가 곧게 뻗거나 아래로 처진 개 사진", "말리지 않은 긴 꼬리를 가진 개"],
        "짧은 꼬리": ["꼬리가 아주 짧거나 없는 개 사진", "짧게 잘린 꼬리를 가진 개"],
    },
}
PROMPT_SETS = {"default": PROMPTS, "body": BODY_PROMPTS}


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
