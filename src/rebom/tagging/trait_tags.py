"""비전 모델(Gemini)로 사진 인상 라인(털 인상, 얼굴 인상)을 고른다 (B 방식: 정해진 보기 + '판단 어려움' + 근거).

성향 라인 시험(2026-11-12). 모델에게 -1~1 숫자를 직접 쓰게 하지 않고, 3단계 보기에서 고르게 한 뒤
코드가 점수로 바꾼다. 같은 사진을 여러 번 물어 평균을 내면 -1, -0.33, 0, 0.33, 1 같은 단계 점수가 된다.
Gemini 3.x 는 보기별 확률(logprobs)을 주지 않아(400 "Logprobs is not enabled for this model") 반복 평균을 쓴다.

얼굴 인상 기준은 사람 답지(lemin, 107장)를 매기며 나온 관찰을 반영했다:
눈매·눈이 주는 인상이 가장 강하고, 늘어진 귀는 순한 쪽 단서지만 선 귀만으로 늠름이 되지는 않는다.

v2 (2026-11-12, v1 채점 후 사용자 결정):
- 털 '중간' 재정의. v1 은 사람 '중간' 21장 중 14장을 '매끈'으로 골랐다(진돗개형 짧고 촘촘한 이중모를
  '몸 윤곽이 보임'으로 읽음). 중간 = 짧거나 중간 길이라도 촘촘하고 두툼한 이중모, 또는 몸은 짧고 장식 털이 있음.
  매끈 = 털이 아주 짧고 피부에 붙어 근육선이 그대로 보임.
- 얼굴 '늠름' 보강. v1 은 사람 '늠름' 24장 중 9장을 '순둥'으로 골랐고 근거가 모두 '크고 동그란 눈'이었다.
  눈이 동그랗더라도 시선이 곧고 눈빛이 또렷하면 늠름으로 본다.
- 3회 반복은 답이 거의 늘 같아 평균 점수가 잘게 나오지 않았다 → 기본 1회.
같은 107장에 맞춰 고친 질문지라 107장 점수는 좋게 나오기 쉽다. 새 사진(trait_holdout)으로 따로 확인한다.

v3 (2026-11-12, 새 40장 채점 후 사용자 결정): 털을 2단계로 줄인다. v2 털은 새 40장에서 72%였고
(107장 82% 는 과적합), 오류가 모두 '중간' 경계에서 났다(양끝을 서로 바꾼 경우는 0). 그래서 털은
복슬복슬/매끈만 태그로 쓰고, 애매하면 '애매함'을 골라 태그를 붙이지 않는다. 사람 답의 '중간'은
채점할 때 '애매함'과 같은 것으로 본다(GOLD_ALIASES). 얼굴 인상은 v2 그대로.

공고의 품종(kindNm)은 입력에 넣지 않는다. 질문지를 바꾸면 PROMPT_VERSION 을 올리고 다시 돌린다.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from statistics import mean
from typing import Sequence

PROMPT_VERSION = "trait-vision-v3"
UNSURE = "판단 어려움"
NO_TAG = "애매함"  # 털: 태그를 붙이지 않는 가운데 보기

AXES = ("fur", "face")
AXIS_NAMES = {"fur": "털 인상", "face": "얼굴 인상"}

# 보기 순서 = (+1 쪽, 0, -1 쪽, 판단 어려움)
OPTIONS: dict[str, tuple[str, ...]] = {
    "fur": ("복슬복슬", NO_TAG, "매끈", UNSURE),
    "face": ("순둥", "중간", "늠름", UNSURE),
}
SCORE: dict[str, dict[str, int]] = {ax: {o[0]: 1, o[1]: 0, o[2]: -1} for ax, o in OPTIONS.items()}

# 사람 답지(3단계)를 지금 보기로 옮기는 표. 털 '중간' = 태그 없음.
GOLD_ALIASES: dict[str, dict[str, str]] = {"fur": {"중간": NO_TAG}}


def normalize_gold(ax: str, value: str) -> str:
    return GOLD_ALIASES.get(ax, {}).get(value, value)


# 화면에 보일 태그 (2026-11-12 사용자 결정). 털은 '복슬복슬'·'짧은 털' 두 가지만 보인다.
# 모델 '매끈'은 '짧은 털'로 보인다: 사람이 '중간'(짧고 촘촘한 이중모)으로 본 털까지 같은 쪽으로 묶으면
# 매끈 태그 정확도가 94% (107장 34/36, 새 40장 15/16). 가운데 보기·판단 어려움은 태그를 붙이지 않는다.
TAG_LABELS: dict[str, dict[str, str]] = {
    "fur": {"복슬복슬": "복슬복슬", "매끈": "짧은 털"},
    "face": {"순둥": "순둥", "늠름": "늠름"},
}


def display_tag(ax: str, value: str) -> str | None:
    """모델이 고른 보기 → 화면 태그. 태그를 붙이지 않으면 None."""
    return TAG_LABELS[ax].get(value)

CRITERIA: dict[str, dict[str, str]] = {
    "fur": {
        "복슬복슬": "털이 길거나 곱슬·부풀어 몸 윤곽이 털에 가려짐",
        NO_TAG: "복슬복슬이나 매끈이라고 확실히 말할 수 없음 (촘촘한 이중모, 중간 길이 털 등). 이 경우 태그를 붙이지 않음",
        "매끈": "털이 아주 짧고 피부에 붙어 근육선·뼈대가 그대로 보임",
        UNSURE: "털이 젖었거나 미용 직후, 너무 어둡거나 작게 찍혀 판단할 수 없음",
    },
    "face": {
        "순둥": "눈빛이 순하고 눈꼬리가 처져 부드러운 인상. 둥근 얼굴·짧은 주둥이·늘어진 귀도 순한 쪽 단서",
        "중간": "눈매가 어느 쪽으로도 뚜렷하지 않음",
        "늠름": "시선이 곧고 눈빛이 또렷하거나 날카로워 단단한 인상. 눈이 동그랗더라도 시선이 곧고 또렷하면 늠름."
        " 길쭉한 주둥이·각진 얼굴이 더해지면 더 뚜렷함",
        UNSURE: "얼굴·눈이 가려졌거나 뒤돌아 있거나 너무 작게 찍힘",
    },
}

SYSTEM_PROMPT = (
    "너는 보호소 유기견 사진에서 입양 희망자가 받는 첫인상을 기록하는 도우미다. "
    "사진에 직접 보이는 것만 근거로 판단한다. 품종을 짐작해서 채우지 않는다. "
    "성격이 아니라 생김새에서 오는 인상만 고른다. 확실히 판단할 수 없으면 '판단 어려움'을 고른다."
)


def _lines(ax: str) -> str:
    return "\n".join(f"- {opt}: {text}" for opt, text in CRITERIA[ax].items())


USER_PROMPT = f"""사진에서 가장 크게, 가운데에 보이는 개 한 마리를 기준으로 두 항목을 고르세요.
사진 한 장에 여러 컷이 이어 붙어 있으면 모든 컷을 참고하세요.

[털 인상]
{_lines("fur")}

[얼굴 인상]
{_lines("face")}
얼굴 인상은 눈매와 눈이 주는 인상을 가장 중요하게 보세요. 귀가 서 있다는 것만으로 늠름을 고르지 마세요.
눈이 크고 동그랗다는 것만으로 순둥을 고르지 마세요. 시선과 눈빛이 순한지 곧고 또렷한지를 보세요.
겁먹음·졸림 같은 순간 표정보다 생김새를 기준으로 보세요.

털 인상은 확실할 때만 복슬복슬이나 매끈을 고르고, 조금이라도 애매하면 '애매함'을 고르세요.

항목마다 evidence 에 사진에서 실제로 본 것을 한국어 한 문장으로 먼저 쓰고, value 에 보기 하나를 그대로 고르세요."""


def response_schema() -> dict:
    """구조화 출력용 JSON 스키마. 근거를 먼저 쓰게 evidence 를 value 앞에 둔다."""
    props = {}
    for ax in AXES:
        props[ax] = {
            "type": "object",
            "properties": {
                "evidence": {"type": "string", "description": f"{AXIS_NAMES[ax]}에 대해 사진에서 본 것, 한 문장"},
                "value": {"type": "string", "enum": list(OPTIONS[ax])},
            },
            "required": ["evidence", "value"],
            "propertyOrdering": ["evidence", "value"],
        }
    return {"type": "object", "properties": props, "required": list(AXES), "propertyOrdering": list(AXES)}


@dataclass(frozen=True)
class TraitAnswer:
    value: str
    evidence: str


def parse_answer(text: str) -> dict[str, TraitAnswer]:
    """모델 응답 JSON → 항목별 답. 보기에 없는 값이면 ValueError."""
    data = json.loads(text)
    out = {}
    for ax in AXES:
        item = data.get(ax) or {}
        value = (item.get("value") or "").strip()
        if value not in OPTIONS[ax]:
            raise ValueError(f"{ax}: 보기에 없는 값 {value!r}")
        out[ax] = TraitAnswer(value=value, evidence=(item.get("evidence") or "").strip())
    return out


def to_score(ax: str, value: str) -> int | None:
    """보기 → +1/0/-1. '판단 어려움'·빈 값은 None."""
    return SCORE[ax].get(value)


def average_score(ax: str, values: Sequence[str]) -> float | None:
    """여러 번 고른 보기의 평균 점수. '판단 어려움'은 빼고, 남은 게 없으면 None."""
    scores = [s for s in (to_score(ax, v) for v in values) if s is not None]
    return mean(scores) if scores else None


def majority(values: Sequence[str]) -> str | None:
    """가장 많이 나온 보기. 동률이면 None."""
    if not values:
        return None
    top = Counter(values).most_common()
    if len(top) > 1 and top[0][1] == top[1][1]:
        return None
    return top[0][0]


def unanimous(values: Sequence[str]) -> str | None:
    """모든 실행이 같은 보기를 골랐고 그게 '판단 어려움'이 아니면 그 보기, 아니면 None."""
    if values and len(set(values)) == 1 and values[0] != UNSURE:
        return values[0]
    return None


def score_to_option(ax: str, score: float | None) -> str | None:
    """평균 점수를 가장 가까운 보기로 되돌린다 (0.5 이상 +1 쪽, -0.5 이하 -1 쪽)."""
    if score is None:
        return None
    pos, mid, neg = OPTIONS[ax][:3]
    if score >= 0.5:
        return pos
    if score <= -0.5:
        return neg
    return mid
