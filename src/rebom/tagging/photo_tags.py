"""사진 태그 사전 v0 (사진에서만 얻는 태그).

평가 계획: Claude Doc "Jev 이미지 태깅 평가 계획".
공고 필드로 이미 있는 태그(축종·털색 계열·나이·크기·품종)는 여기서 만들지 않는다.
축종·털색은 정답이 공짜라서 모델이 사진을 제대로 보는지 확인하는 점검용으로만 쓴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

VERSION = "photo-tags-v0"
UNKNOWN = "알 수 없음"

# 털색 비율에 쓰는 기본 색. 삼색·고등어 같은 무늬는 이 색들의 조합으로 본다.
PALETTE = ("흰색", "검정", "회색", "갈색", "크림·황토", "주황")


@dataclass(frozen=True)
class ChoiceTag:
    key: str
    question: str
    choices: tuple[str, ...]  # 마지막은 항상 UNKNOWN
    check_only: bool = False  # True면 점검용(정답이 공고 필드)


CHOICE_TAGS: tuple[ChoiceTag, ...] = (
    ChoiceTag("fur_length", "사진 속 동물의 털 길이는 어떻습니까?", ("짧음", "김", UNKNOWN)),
    ChoiceTag("injury", "사진에서 눈에 보이는 상처나 외상이 있습니까?", ("있음", "없음", UNKNOWN)),
    ChoiceTag(
        "photo_quality",
        "사진에서 동물이 잘 보입니까?",
        ("잘 보임", "가려짐·흐림", "동물 없음", UNKNOWN),
    ),
    ChoiceTag("color_main", "사진 속 동물에서 가장 넓은 털색은 무엇입니까?", (*PALETTE, UNKNOWN)),
    ChoiceTag("species", "사진 속 동물의 종류는 무엇입니까?", ("개", "고양이", "기타", UNKNOWN), check_only=True),
)
TAGS_BY_KEY = {t.key: t for t in CHOICE_TAGS}

# 닮은 품종 후보(개). 국내 견종(진도견·풍산견)은 꼭 포함한다. 공고 kindNm 표기와 맞춘다.
DOG_BREEDS = (
    "말티즈", "푸들", "포메라니안", "비숑 프리제", "시츄", "치와와", "요크셔 테리어",
    "진도견", "풍산견", "시바", "웰시 코기", "닥스훈트", "비글", "슈나우저", "스피츠",
    "보더 콜리", "골든 리트리버", "래브라도 리트리버", "시베리안 허스키", "차우차우",
    "포인터", "도베르만", "잭 러셀 테리어", "핏불테리어",
)
BREED_ALIASES = {
    "미디엄 푸들": "푸들", "토이 푸들": "푸들", "스탠다드 푸들": "푸들",
    "웰시 코기 카디건": "웰시 코기", "웰시 코기 펨브로크": "웰시 코기",
    "저먼 쇼트헤어드 포인터": "포인터", "진돗개": "진도견", "시바견": "시바",
    "재패니즈 스피츠": "스피츠", "저먼 스피츠": "스피츠",
    # 공고 표기 ↔ 후보 목록 표기 (2026-11-03 품종견 60건에서 확인)
    "라브라도 리트리버": "래브라도 리트리버", "슈나우져": "슈나우저", "미니어쳐 슈나우져": "슈나우저",
    "미니어처 슈나우저": "슈나우저", "미니어쳐 닥스훈트": "닥스훈트", "미니어처 닥스훈트": "닥스훈트",
}
NOT_A_BREED = ("믹스", "기타", "한국 고양이")


def gold_breed(kind_nm: str | None) -> str | None:
    """공고 kindNm 에서 채점용 품종 정답. 믹스·기타면 None(정답 없음)."""
    if not kind_nm or any(w in kind_nm for w in NOT_A_BREED):
        return None
    name = kind_nm.strip()
    return BREED_ALIASES.get(name, name)


# colorCd 자유 표기 → 언급된 기본 색. 점검용(사진의 주색이 이 안에 드는지).
_COLOR_WORDS = (
    ("삼색", ("흰색", "검정", "주황")),
    ("고등어", ("회색", "검정")),
    ("젖소", ("흰색", "검정")),
    ("흑백", ("흰색", "검정")),
    ("호반", ("갈색", "검정")),
    ("치즈", ("주황",)),
    ("주황", ("주황",)),
    ("오렌지", ("주황",)),
    ("흰", ("흰색",)), ("백", ("흰색",)),
    ("검", ("검정",)), ("흑", ("검정",)),
    ("회", ("회색",)), ("청", ("회색",)),
    ("갈", ("갈색",)), ("초코", ("갈색",)),
    ("황", ("크림·황토",)), ("크림", ("크림·황토",)), ("베이지", ("크림·황토",)),
    ("레몬", ("크림·황토",)), ("금", ("크림·황토",)), ("노", ("크림·황토",)),
)


def colors_in_color_cd(color_cd: str | None) -> set[str]:
    """colorCd 에 언급된 기본 색 집합. 못 읽으면 빈 집합."""
    if not color_cd:
        return set()
    text = color_cd
    found: set[str] = set()
    for word, colors in _COLOR_WORDS:
        if word in text:
            found.update(colors)
            text = text.replace(word, " ")
    return found


def parse_ratio_label(text: str | None) -> dict[str, float]:
    """라벨 시트의 털색 비율 칸 "흰색:60, 갈색:40" → {"흰색": 0.6, "갈색": 0.4}. 합이 1이 되게 맞춘다."""
    if not text or not text.strip():
        return {}
    out: dict[str, float] = {}
    for part in re.split(r"[,/]", text):
        if ":" not in part:
            continue
        name, val = part.split(":", 1)
        name = name.strip()
        if name not in PALETTE:
            raise ValueError(f"팔레트에 없는 색: {name!r} (허용: {', '.join(PALETTE)})")
        out[name] = out.get(name, 0.0) + float(val.strip().rstrip("%"))
    total = sum(out.values())
    return {k: v / total for k, v in out.items()} if total else {}
