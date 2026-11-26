"""사진 태그 사전: 태그 코드, 화면 이름, 판정 기준, 검색·화면 사용 여부.

판정 기준 문장은 사람이 읽는 설명이다(docs/data_contract.md 13절과 같은 내용). 모델에게 보내는 질문 문구는
trait_tags.py·breed_tags.py 에 따로 있고, 평가 때 문구를 그대로 쓴다.

모든 태그는 같은 사진에 2번 물어 두 답이 같을 때만 붙인다. 인상(귀염·늠름)은 태깅하지 않는다(2026-11-19 결정).
'귀염'은 어떤 경우에도 검색 태그로 만들지 않는다(2026-11-16 결정).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TagDef:
    code: str
    label: str  # 화면·검색에 쓰는 이름
    criterion: str  # 최종 판정 기준
    searchable: bool = True
    displayable: bool = True


TAGS: dict[str, TagDef] = {
    t.code: t
    for t in (
        TagDef("fur.fluffy", "복슬복슬", "털이 길거나 곱슬·부풀어 몸 윤곽이 털에 가려짐. 2회 모두 '복슬복슬'"),
        TagDef(
            "fur.short",
            "짧은 털",
            "모델이 '매끈'을 고름: 털이 짧아 몸 윤곽이 보임(짧고 촘촘한 이중모 포함). 2회 모두 '매끈'",
        ),
        TagDef("ear.drop", "늘어진 귀", "귀 전체가 머리 옆으로 처져 내려옴. 2회 모두 '늘어짐'"),
        TagDef("ear.semi", "반쯤 선 귀", "귀뿌리는 서 있고 끝이 앞이나 옆으로 접힘. 2회 모두 '반쯤 섬'"),
        TagDef("ear.prick", "선 귀", "귀가 끝까지 위로 곧게 섬. 2회 모두 '섬'"),
        TagDef("eye.round", "둥근 눈매", "크고 동그란 눈. 2회 모두 '둥글함'"),
        TagDef("eye.sharp", "날카로운 눈매", "눈이 가늘고 눈꼬리가 길거나 올라감. 2회 모두 '날카로움'"),
    )
}

# 모델 보기 → 태그 코드 (trait-vision-v10)
FUR_CODES = {"복슬복슬": "fur.fluffy", "매끈": "fur.short"}
EAR_CODES = {"늘어짐": "ear.drop", "반쯤 섬": "ear.semi", "섬": "ear.prick"}
EYE_CODES = {"둥글함": "eye.round", "날카로움": "eye.sharp"}

BREED_PREFIX = "breed."
BREED_CRITERION = "두 회차 모두 닮은 품종 3위 안에 들고, 두 회차 평균 유사도가 90 이상(강 신호)"


def breed_code(name: str) -> str:
    """품종 태그 코드. 62종 표준 한국어 이름을 그대로 쓴다(예: breed.진도견)."""
    return f"{BREED_PREFIX}{name}"


def breed_tag(name: str) -> TagDef:
    """품종 태그는 검색·챗봇에만 쓰고 화면에는 보이지 않는다(2026-11-17 결정)."""
    return TagDef(breed_code(name), name, BREED_CRITERION, searchable=True, displayable=False)


def get(code: str) -> TagDef:
    if code.startswith(BREED_PREFIX):
        return breed_tag(code[len(BREED_PREFIX):])
    return TAGS[code]
