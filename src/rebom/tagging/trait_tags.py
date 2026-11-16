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

v4 (2026-11-12, 994장 확장 평가셋 2회 실행 후 사용자 결정): 늠름을 생김새로 좁힌다. v3 늠름은 실행마다
흔들렸고(1회차 늠름 121장 중 2회 모두 늠름 78장, 순둥↔늠름 뒤집힘 62건), 새 40장 늠름 태그 정확도 56%.
원인은 '시선이 곧고 눈빛이 또렷하면 늠름'이 생김새가 아니라 찍힌 순간의 상태를 보게 한 것.
늠름 = 아몬드형·눈꼬리 올라간 눈 / 길고 곧은 주둥이 / 날렵하거나 각진 얼굴선 중 두 가지 이상.
곧은 시선·선 귀는 보조 단서일 뿐이고, 얼굴이 아직 둥근 어린 강아지는 늠름 대신 중간.

v5 (2026-11-12, dev 200장 채점 후 사용자 결정): 얼굴 축을 순둥↔늠름에서 귀염↔늠름으로 바꾸고 생김새만 본다.
늠름은 순둥의 반대가 아니다: 순둥은 기질·눈빛이고 늠름은 생김새라 한 축에 놓으면 섞인다.
dev 200장 채점에서 v4 는 사람 늠름의 절반을 놓쳤고, 순한 눈빛·숙인 고개를 순둥으로 읽은 탓이었다.
귀염 = 둥근 얼굴·뭉툭한 주둥이·아기 같은 비율(어린 강아지 포함), 늠름 = 어른스럽고 단단한 생김새.
눈빛·표정·시선·자세·선 귀는 찍힌 순간의 상태라 보지 않는다. 털은 v3 그대로.

v6 (2026-11-13, test 300장 채점·납득 검토 후 사용자 결정): 얼굴만 고친다(털은 v5 그대로).
test 300 에서 늠름 태그 정확도 49%(51/100). 답과 다른 늠름 49개 중 47개 근거에 '성견·다 자란·성숙'이 있었다:
얼굴이 아니라 나이·몸 전체를 보고 늠름으로 기운 것. v5 늠름 정의의 '어른스럽고 단단한 생김새'가 이를 부추겼다.
늠름 = 얼굴에서 길고 곧은 주둥이 / 갸름하거나 각진 얼굴선 / 아몬드형 눈 중 두 가지 이상.
얼굴(머리)만 보고 몸 크기·체형·다리·나이는 보지 않는다. 다 큰 개라는 것만으로 늠름이 아니다.
고개를 숙이거나 위에서 찍혀 주둥이가 길어 보이는 착시(#142)도 문구로 막는다.
근거(evidence)에도 얼굴 부위만 쓰게 한다.

v7 (2026-11-16, 사용자 결정): 얼굴은 모델이 귀염/늠름을 직접 고르지 않는다. v6 새 사진 100장에서
같은 사진의 1·2회차 답이 귀염↔늠름으로 뒤집히고 근거 문장도 그 답에 맞춰 정반대로 쓰였다(예: '주둥이가 길고
갸름' ↔ '주둥이가 짧고 둥긂'). 근거가 판단 이유가 아니라 답에 맞춘 설명이라 근거로 오류를 가릴 수 없었다.
그래서 얼굴은 특징 칸(어린 강아지 여부, 주둥이, 얼굴선, 눈)을 고르게 하고, 태그는 코드가 정한다
(face_from_features: 어린 강아지면 귀염, 늠름 쪽 특징 2개 이상이면 늠름, 귀염 쪽 2개 이상이면 귀염, 아니면 중간,
안 보임 2개 이상이면 판단 어려움). 털은 v5 그대로. v6 코드 백업: data/eval/photo_tags/_trait_tags_v6_backup.py.
귀·표정 칸(ears, expression)도 받되 태그 규칙에는 넣지 않고 기록만 한다(2026-11-16 lemin 제안: 요소를 따로 뽑아 애매한 요소를 버리고 합치는 방식 평가용).

v8 (2026-11-16, 사용자 결정): 얼굴 요소 네 가지를 각각 태그로 붙이고, 인상도 요소를 합쳐 만든다.
귀(늘어짐/반쯤 섬/섬), 표정(순함/보통/당당함), 주둥이(짧음/김), 눈매(둥글함/날카로움). 얼굴선 칸은 뺀다.
v7 100장에서 주둥이는 '보통'이 70%였고 칸별 1·2회차 일치는 귀 97%, 주둥이 94%, 표정 89%, 눈 77%였다.
주둥이·눈매는 애매하면 '애매함'(태그 없음). 화면 요소 태그는 element_tags, 2회 일치 칸만 쓰려면 agreed_features.
인상 = 애매한 요소(가운데 보기·안 보임·회차 간 불일치)를 버리고 남은 요소의 귀염 쪽·늠름 쪽 수 차이가 2 이상인 쪽.
v7 코드 백업: data/eval/photo_tags/_trait_tags_v7_backup.py.

공고의 품종(kindNm)은 입력에 넣지 않는다. 질문지를 바꾸면 PROMPT_VERSION 을 올리고 다시 돌린다.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from statistics import mean
from typing import Sequence

PROMPT_VERSION = "trait-vision-v8"
UNSURE = "판단 어려움"
NO_TAG = "애매함"  # 털: 태그를 붙이지 않는 가운데 보기

AXES = ("fur", "face")
AXIS_NAMES = {"fur": "털 인상", "face": "얼굴 인상"}

# 보기 순서 = (+1 쪽, 0, -1 쪽, 판단 어려움)
OPTIONS: dict[str, tuple[str, ...]] = {
    "fur": ("복슬복슬", NO_TAG, "매끈", UNSURE),
    "face": ("귀염", "중간", "늠름", UNSURE),
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
    "face": {"귀염": "귀염", "늠름": "늠름"},
}


HIDDEN = "안 보임"
AMBIG = "애매함"
# 얼굴 요소 칸 (v8). 보기 순서 = (귀염 쪽, 가운데, 늠름 쪽, 안 보임). puppy 만 예/아니오/모름.
FACE_FEATURES: dict[str, tuple[str, ...]] = {
    "puppy": ("예", "아니오", "모름"),
    "ears": ("늘어짐", "반쯤 섬", "섬", HIDDEN),
    "expression": ("순함", "보통", "당당함", HIDDEN),
    "muzzle": ("짧음", AMBIG, "김", HIDDEN),
    "eyes": ("둥글함", AMBIG, "날카로움", HIDDEN),
}
FEATURE_NAMES = {"puppy": "어린 강아지", "ears": "귀", "expression": "표정", "muzzle": "주둥이", "eyes": "눈매"}
ELEMENT_KEYS = ("ears", "expression", "muzzle", "eyes")
# 화면에 보일 요소 태그. 가운데 보기(보통·애매함)와 안 보임은 태그 없음. 귀 '반쯤 섬'은 태그로 보이되 인상 합치기에는 안 씀.
ELEMENT_TAGS: dict[str, tuple[str, ...]] = {
    "ears": ("늘어짐", "반쯤 섬", "섬"),
    "expression": ("순함", "당당함"),
    "muzzle": ("짧음", "김"),
    "eyes": ("둥글함", "날카로움"),
}
IMPRESSION_MARGIN = 2  # 귀염 쪽 요소 수와 늠름 쪽 요소 수의 차이가 이만큼 이상일 때만 인상 태그


def element_tags(f: dict[str, str]) -> dict[str, str]:
    """요소 칸 → 화면에 보일 요소 태그 (태그 없는 요소는 빠짐)."""
    return {k: f[k] for k in ELEMENT_KEYS if f.get(k) in ELEMENT_TAGS[k]}


def agreed_features(runs: Sequence[dict[str, str]]) -> dict[str, str]:
    """여러 실행에서 모두 같은 값이 나온 칸만 남긴다 (회차마다 다른 칸은 애매한 요소로 버림)."""
    if not runs:
        return {}
    return {k: runs[0][k] for k in FACE_FEATURES if all(r.get(k) == runs[0].get(k) for r in runs)}


def face_from_features(f: dict[str, str]) -> str:
    """요소 칸 → 얼굴 인상(귀염/중간/늠름/판단 어려움). 가운데 보기·안 보임·빠진 칸은 버리고 남은 요소를 합친다.

    어린 강아지면 귀염. 아니면 귀염 쪽(짧음·둥글함·늘어짐·순함)과 늠름 쪽(김·날카로움·섬·당당함) 수의
    차이가 IMPRESSION_MARGIN 이상인 쪽. 요소가 3개 이상 안 보이면 판단 어려움, 나머지는 중간(태그 없음).
    """
    if sum(f.get(k) == HIDDEN for k in ELEMENT_KEYS) >= 3:
        return UNSURE
    if f.get("puppy") == "예":
        return "귀염"
    cute = sum(f.get(k) == FACE_FEATURES[k][0] for k in ELEMENT_KEYS)
    brave = sum(f.get(k) == FACE_FEATURES[k][2] for k in ELEMENT_KEYS)
    if cute - brave >= IMPRESSION_MARGIN:
        return "귀염"
    if brave - cute >= IMPRESSION_MARGIN:
        return "늠름"
    return "중간"


def features_text(f: dict[str, str]) -> str:
    return " · ".join(f"{FEATURE_NAMES[k]} {f.get(k, '')}" for k in FACE_FEATURES)


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
        "귀염": "얼굴이 둥글고 주둥이가 짧거나 뭉툭함, 눈이 크고 둥긂, 볼이 통통함. 어린 강아지나 털이 얼굴을 덮은 소형견 대부분",
        "중간": "얼굴이 어느 쪽으로도 뚜렷하지 않음 (주둥이 길이 보통, 얼굴선이 둥글지도 날렵하지도 않음)."
        " 다 큰 개도 얼굴에 늠름 특징이 분명하지 않으면 여기",
        "늠름": "얼굴 자체가 날렵하고 각짐. 길고 곧게 뻗은 주둥이, 갸름하거나 각진 얼굴선, 아몬드형 눈 중"
        " 두 가지 이상이 얼굴에서 분명히 보임 (진도·시바·셰퍼드형 얼굴)",
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
    """구조화 출력용 JSON 스키마. 근거를 먼저 쓰게 evidence 를 value 앞에 둔다."""
    props = {}
    for ax in AXES:
        if ax == "face":
            props[ax] = {
                "type": "object",
                "properties": {k: {"type": "string", "enum": list(v), "description": FEATURE_NAMES[k]}
                               for k, v in FACE_FEATURES.items()},
                "required": list(FACE_FEATURES),
                "propertyOrdering": list(FACE_FEATURES),
            }
            continue
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
    features: dict | None = None


def parse_answer(text: str) -> dict[str, TraitAnswer]:
    """모델 응답 JSON → 항목별 답. 보기에 없는 값이면 ValueError."""
    data = json.loads(text)
    out = {}
    for ax in AXES:
        item = data.get(ax) or {}
        if ax == "face":
            feats = {}
            for k, opts in FACE_FEATURES.items():
                v = (item.get(k) or "").strip()
                if v not in opts:
                    raise ValueError(f"face.{k}: 보기에 없는 값 {v!r}")
                feats[k] = v
            out[ax] = TraitAnswer(value=face_from_features(feats), evidence=features_text(feats), features=feats)
            continue
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
