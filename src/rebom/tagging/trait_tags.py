"""사진 특징 태그(털·귀·눈매) 질문지와 판정 규칙. prompt_version trait-vision-v10.

질문 문구와 응답 스키마는 평가 때의 trait-vision-v8(exp/embed-gemini-api 9492e63, src/rebom/tagging/trait_tags.py)과
글자까지 같다. 평가 결과(최종 답지 100장, 2026-11-16·11-20)를 그대로 쓰기 위해서다.
v8 과 다른 것은 판정 규칙뿐이라 이름을 v10 으로 따로 둔다.
- 인상(귀염·늠름)은 판정하지 않는다(2026-11-19 결정). 질문지의 어린 강아지·표정·주둥이 칸은 문구를 유지하려고
  남겨 두고, 답은 원답 기록에만 남긴다.
- 태그는 털·귀·눈매 세 가지이고, 2회 답이 같을 때만 붙인다. '애매함'·'안 보임'·'판단 어려움'은 태그 없음.

공고의 품종(kindNm)은 모델에 보내지 않는다. 질문지를 바꾸면 PROMPT_VERSION 을 올리고 다시 태깅한다.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from rebom.tagging.tag_codes import EAR_CODES, EYE_CODES, FUR_CODES

PROMPT_VERSION = "trait-vision-v10"
TASK = "photo_trait"
UNSURE = "판단 어려움"
NO_TAG = "애매함"
HIDDEN = "안 보임"
AMBIG = "애매함"

AXES = ("fur", "face")
AXIS_NAMES = {"fur": "털 인상", "face": "얼굴 인상"}
FUR_OPTIONS = ("복슬복슬", NO_TAG, "매끈", UNSURE)

# 얼굴 요소 칸 (v8 그대로). 태그에 쓰는 칸은 ears·eyes 뿐이다.
FACE_FEATURES: dict[str, tuple[str, ...]] = {
    "puppy": ("예", "아니오", "모름"),
    "ears": ("늘어짐", "반쯤 섬", "섬", HIDDEN),
    "expression": ("순함", "보통", "당당함", HIDDEN),
    "muzzle": ("짧음", AMBIG, "김", HIDDEN),
    "eyes": ("둥글함", AMBIG, "날카로움", HIDDEN),
}
FEATURE_NAMES = {"puppy": "어린 강아지", "ears": "귀", "expression": "표정", "muzzle": "주둥이", "eyes": "눈매"}

FUR_CRITERIA = {
    "복슬복슬": "털이 길거나 곱슬·부풀어 몸 윤곽이 털에 가려짐",
    NO_TAG: "복슬복슬이나 매끈이라고 확실히 말할 수 없음 (촘촘한 이중모, 중간 길이 털 등). 이 경우 태그를 붙이지 않음",
    "매끈": "털이 아주 짧고 피부에 붙어 근육선·뼈대가 그대로 보임",
    UNSURE: "털이 젖었거나 미용 직후, 너무 어둡거나 작게 찍혀 판단할 수 없음",
}

SYSTEM_PROMPT = (
    "너는 보호소 유기견 사진에서 입양 희망자가 받는 첫인상을 기록하는 도우미다. "
    "사진에 직접 보이는 것만 근거로 판단한다. 품종을 짐작해서 채우지 않는다. "
    "성격이 아니라 생김새에서 오는 인상만 고른다. 확실히 판단할 수 없으면 '판단 어려움'을 고른다."
)

_FUR_LINES = "\n".join(f"- {opt}: {text}" for opt, text in FUR_CRITERIA.items())

USER_PROMPT = f"""사진에서 가장 크게, 가운데에 보이는 개 한 마리를 기준으로 두 항목을 고르세요.
사진 한 장에 여러 컷이 이어 붙어 있으면 모든 컷을 참고하세요.

[털 인상]
{_FUR_LINES}

[얼굴 요소] 각 칸에서 하나씩 고르세요. 얼굴 인상(귀염/늠름)은 고르지 않습니다.
- puppy (어린 강아지인가): 예 = 생후 몇 달 된 강아지(몸에 비해 머리·발이 크고 아기 같은 비율) / 아니오 = 다 자란 개 / 모름
- ears (귀 모양): 늘어짐 = 귀가 아래로 늘어짐 / 반쯤 섬 = 귀가 서다가 끝이 접힘 / 섬 = 귀가 곧게 섬 / 안 보임
- expression (이 사진의 표정): 순함 = 눈빛이 순하고 부드럽거나 입을 벌리고 웃는 듯함 / 보통 / 당당함 = 눈빛이 또렷하고 곧게 바라봄 / 안 보임
- muzzle (주둥이 길이, 머리 전체와 비교): 짧음 = 짧고 뭉툭함 / 애매함 / 김 = 길고 곧게 뻗음 / 안 보임
- eyes (눈매, 눈의 생김새): 둥글함 = 크고 동그란 눈 / 애매함 / 날카로움 = 가늘고 눈꼬리가 길거나 올라감 / 안 보임
얼굴 요소는 머리(얼굴)만 보세요. 몸 크기, 체형, 다리, 나이는 보지 마세요.
다 큰 개라는 것만으로 주둥이가 길거나 눈매가 날카로운 것이 아닙니다. 각 칸은 실제로 보이는 모양만 고르세요.
고개를 숙이거나 위에서 찍히면 주둥이가 실제보다 길어 보입니다. 주둥이 길이는 머리 전체와 비교해서 보세요.
눈매 칸은 눈의 모양만 보고, 눈빛(순한지 또렷한지)은 expression 칸에서만 고르세요.
각 칸은 따로 판단하세요. 다른 칸의 답에 맞추려고 고르지 마세요. 확실하지 않으면 '애매함'이나 '보통'을 고르세요.

털 인상은 확실할 때만 복슬복슬이나 매끈을 고르고, 조금이라도 애매하면 '애매함'을 고르세요.

털 인상은 evidence 에 사진에서 실제로 본 것을 한국어 한 문장으로 먼저 쓰고, value 에 보기 하나를 그대로 고르세요."""


def response_schema() -> dict:
    """구조화 출력용 JSON 스키마(v8 그대로). 근거를 먼저 쓰게 evidence 를 value 앞에 둔다."""
    return {
        "type": "object",
        "properties": {
            "fur": {
                "type": "object",
                "properties": {
                    "evidence": {"type": "string", "description": f"{AXIS_NAMES['fur']}에 대해 사진에서 본 것, 한 문장"},
                    "value": {"type": "string", "enum": list(FUR_OPTIONS)},
                },
                "required": ["evidence", "value"],
                "propertyOrdering": ["evidence", "value"],
            },
            "face": {
                "type": "object",
                "properties": {
                    k: {"type": "string", "enum": list(v), "description": FEATURE_NAMES[k]}
                    for k, v in FACE_FEATURES.items()
                },
                "required": list(FACE_FEATURES),
                "propertyOrdering": list(FACE_FEATURES),
            },
        },
        "required": list(AXES),
        "propertyOrdering": list(AXES),
    }


@dataclass(frozen=True)
class TraitAnswer:
    """한 회차의 답. fur 는 보기 하나, features 는 얼굴 요소 칸 다섯 개."""

    fur: str
    fur_evidence: str
    features: dict[str, str]

    def to_dict(self) -> dict:
        return {"fur": self.fur, "fur_evidence": self.fur_evidence, "features": dict(self.features)}


def parse_answer(text: str | dict) -> TraitAnswer:
    """모델 응답 JSON → TraitAnswer. 보기에 없는 값이면 ValueError."""
    data = json.loads(text) if isinstance(text, str) else text
    fur = (data.get("fur") or {})
    fur_value = (fur.get("value") or "").strip()
    if fur_value not in FUR_OPTIONS:
        raise ValueError(f"fur: 보기에 없는 값 {fur_value!r}")
    face = data.get("face") or {}
    feats: dict[str, str] = {}
    for k, opts in FACE_FEATURES.items():
        v = (face.get(k) or "").strip()
        if v not in opts:
            raise ValueError(f"face.{k}: 보기에 없는 값 {v!r}")
        feats[k] = v
    return TraitAnswer(fur=fur_value, fur_evidence=(fur.get("evidence") or "").strip(), features=feats)


def decide_tags(runs: Sequence[TraitAnswer]) -> list[str]:
    """2회(이상) 답 → 태그 코드 목록. 모든 회차가 같은 보기를 골랐고 그 보기가 태그일 때만 붙인다."""
    if len(runs) < 2:
        return []
    codes: list[str] = []
    furs = {r.fur for r in runs}
    if len(furs) == 1 and (fur := furs.pop()) in FUR_CODES:
        codes.append(FUR_CODES[fur])
    for key, table in (("ears", EAR_CODES), ("eyes", EYE_CODES)):
        values = {r.features.get(key) for r in runs}
        if len(values) == 1 and (v := values.pop()) in table:
            codes.append(table[v])
    return codes
