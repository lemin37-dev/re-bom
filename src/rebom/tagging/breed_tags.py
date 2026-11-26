"""믹스견 닮은 품종 Top3 질문지와 판정 규칙. prompt_version breed-vision-v2-open.

질문 문구와 스키마는 평가 때의 breed-vision-v2 open_know(모든 품종 × 모델 지식, 62종은 참고 목록)와 같다
(PC data/eval/photo_tags/_breed_vision/predict_breed_v2.py 의 build("open_know", ...)).
특징표(breed_features_v2.md)는 쓰지 않는다(정확도↓·비용 5배, 2026-11-17 결정).

판정:
- Top3 원값(두 회차의 순위·품종·유사도·근거)은 모두 Gold 에 남긴다. 챗봇은 이 원값을 쓴다.
- 검색 태그는 강 신호만: 두 회차 모두 3위 안에 든 품종 중 두 회차 평균 유사도가 90 이상(2026-11-19 결정).
- 품종은 모델이 고른다. 62종은 질문지 참고 목록이자 같은 품종의 다른 표기를 맞추는 표(normalize)일 뿐이고, 목록 밖 품종은 모델이 쓴 이름 그대로 둔다.
- 품종은 강 신호라도 화면에 보이지 않는다(displayable false).
- 처리 기록 status: OK = 강 신호 하나 이상, NO_RESULT = 강 신호 없음(약한 품종 행은 남을 수 있음), tag_count = 강 신호 수.
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

PROMPT_VERSION = "breed-vision-v2-open"
TASK = "photo_breed"
STRONG_SIMILARITY = 90
LOOKS = ("순종으로 보임", "믹스로 보임", "모름")
NONE = "해당 없음"

SYSTEM_PROMPT = (
    "당신은 유기동물 보호소 사진을 보고 개의 생김새가 어떤 품종을 닮았는지 판단하는 도우미입니다. "
    "사진에 직접 보이는 생김새(얼굴형, 귀, 주둥이, 눈, 털 길이와 질감, 털색 무늬, 꼬리, 체형)만 근거로 삼습니다. "
    "보호소 개는 대부분 믹스견이므로, 닮은 품종을 고르되 닮은 정도를 솔직하게 적습니다."
)
SIM_RULE = (
    "similarity는 이 개의 생김새가 그 품종의 전형적인 생김새와 얼마나 닮았는지 0~100 정수로 적으세요. "
    "기준: 90 이상 = 그 품종 순종으로 보임, 60~89 = 주요 특징 대부분이 닮음, 30~59 = 일부 특징만 닮음, 30 미만 = 거의 닮지 않음. "
    "순위는 similarity가 높은 순서여야 합니다."
)
COMMON = (
    "사진에서 가장 크게, 가운데에 보이는 개 한 마리를 기준으로 답하세요.\n"
    "1) evidence: 사진에 보이는 생김새를 한두 문장으로 먼저 쓰세요.\n"
    "2) look: 한 품종의 특징이 뚜렷하면 '순종으로 보임', 여러 품종이 섞였거나 특정 품종으로 보기 어려우면 '믹스로 보임', 사진으로 판단할 수 없으면 '모름'.\n"
    "3) top: 생김새가 가장 닮은 품종 3개를 rank 1~3으로 적으세요. 각 품종에 similarity와 reason(그 품종의 어떤 특징이 맞는지 짧게)을 붙이세요. "
    "닮은 품종이 3개가 안 되면 나머지는 breed '해당 없음', similarity 0.\n"
)

# 모델이 쓴 이름 → 62종 표준 이름 (평가 비교 스크립트 compare_openrouter.py 와 같은 표)
ALIAS = {
    "진돗개": "진도견", "진도개": "진도견", "진도": "진도견", "래브라도리트리버": "라브라도 리트리버", "래브라도": "라브라도 리트리버",
    "비숑": "비숑 프리제", "비숑프리세": "비숑 프리제", "시바견": "시바", "시바이누": "시바", "재패니즈스피츠": "스피츠",
    "저먼셰퍼드": "셰퍼드", "저먼셰퍼드독": "셰퍼드", "독일셰퍼드": "셰퍼드", "슈나우저": "슈나우져", "미니어처슈나우저": "슈나우져",
    "미니어처핀셔": "미니어쳐 핀셔", "미니핀": "미니어쳐 핀셔", "요크셔테리어": "요크셔 테리어", "웰시코기": "웰시 코기 펨브로크",
    "펨브로크웰시코기": "웰시 코기 펨브로크", "카디건웰시코기": "웰시 코기 카디건", "시베리안허스키": "시베리안 허스키",
    "허스키": "시베리안 허스키", "알래스칸말라뮤트": "말라뮤트", "잉글리시코커스패니얼": "코카 스파니엘",
    "코커스패니얼": "코카 스파니엘", "잉글리시포인터": "포인터", "아메리칸핏불테리어": "핏불테리어", "핏불": "핏불테리어",
    "벨지안말리노이즈": "마리노이즈", "말리노이즈": "마리노이즈", "빠삐용": "빠삐용(콘티넨탈 토이 스파니엘)",
    "파피용": "빠삐용(콘티넨탈 토이 스파니엘)", "이탈리안그레이하운드": "이탈리안 그레이 하운드", "그레이하운드": "그레이 하운드",
    "프렌치불도그": "프렌치 불독", "잉글리시불독": "불독", "불도그": "불독", "도사견": "도사", "토사": "도사",
    "풍산개": "풍산견", "동경이": "동경견", "경주개동경이": "동경견", "삽살이": "삽살개", "토이푸들": "푸들",
    "미니어처푸들": "푸들", "스탠다드푸들": "푸들", "닥스훈드": "닥스훈트", "미니어처닥스훈트": "닥스훈트", "포메": "포메라니안",
    "저먼스피츠": "미텔 스피츠", "웨스트시베리안라이카": "라이카", "그레이트피레니즈": "그레이트 피레니즈",
}


def _squash(s: str | None) -> str:
    return re.sub(r"[\s\-·()]", "", s or "").lower()


@lru_cache(maxsize=1)
def load_candidates() -> tuple[str, ...]:
    """한국 보호소에 자주 나오는 62종 (3년치 공고 빈도순, 2026-11-17 집계)."""
    text = resources.files("rebom.tagging").joinpath("data/breed_candidates_3y.json").read_text(encoding="utf-8")
    return tuple(b["name"] for b in json.loads(text)["breeds"])


@lru_cache(maxsize=1)
def _lookup() -> tuple[dict[str, str], dict[str, str]]:
    return {_squash(c): c for c in load_candidates()}, {_squash(k): v for k, v in ALIAS.items()}


def normalize(name: str | None) -> str | None:
    """모델이 쓴 품종 이름 → 62종 표준 이름. '해당 없음'·빈 값은 None, 목록 밖이면 원래 이름(앞뒤 공백 제거)."""
    if not name or name.strip() == NONE:
        return None
    cands, aliases = _lookup()
    s = _squash(name)
    if s in cands:
        return cands[s]
    if s in aliases:
        return aliases[s]
    for k, v in cands.items():
        if len(k) >= 3 and (k in s or s in k):
            return v
    return name.strip()


def build_prompt() -> str:
    cands = load_candidates()
    return (
        COMMON + SIM_RULE + "\n"
        + "품종은 세계의 모든 개 품종 중에서 고를 수 있습니다. 한국에서 흔히 쓰는 한국어 품종명으로 적고, "
        "'믹스'·'잡종' 같은 말이나 두 품종을 합친 이름은 쓰지 마세요.\n"
        "참고로 한국 보호소에 자주 나오는 품종은 다음과 같습니다(이 밖의 품종도 가능): " + ", ".join(cands) + "\n"
        + "\n각 품종의 생김새는 당신이 알고 있는 품종 지식으로 판단하세요.\n"
    )


def response_schema() -> dict:
    item = {
        "type": "object",
        "properties": {
            "rank": {"type": "integer"},
            "breed": {"type": "string"},
            "similarity": {"type": "integer"},
            "reason": {"type": "string"},
        },
        "required": ["rank", "breed", "similarity", "reason"],
        "propertyOrdering": ["rank", "breed", "similarity", "reason"],
    }
    return {
        "type": "object",
        "properties": {
            "evidence": {"type": "string"},
            "look": {"type": "string", "enum": list(LOOKS)},
            "top": {"type": "array", "items": item, "minItems": 3, "maxItems": 3},
        },
        "required": ["evidence", "look", "top"],
        "propertyOrdering": ["evidence", "look", "top"],
    }


@dataclass(frozen=True)
class BreedPick:
    rank: int
    breed: str  # 표준 이름
    raw_breed: str  # 모델이 쓴 이름
    similarity: int
    reason: str


@dataclass(frozen=True)
class BreedAnswer:
    evidence: str
    look: str
    top: tuple[BreedPick, ...]

    def similarities(self) -> dict[str, int]:
        """표준 이름 → 유사도 (같은 품종이 두 번 나오면 앞 순위 값)."""
        out: dict[str, int] = {}
        for p in self.top:
            out.setdefault(p.breed, p.similarity)
        return out

    def to_dict(self) -> dict:
        return {
            "evidence": self.evidence,
            "look": self.look,
            "top": [
                {"rank": p.rank, "breed": p.breed, "raw_breed": p.raw_breed, "similarity": p.similarity, "reason": p.reason}
                for p in self.top
            ],
        }


def parse_answer(text: str | dict) -> BreedAnswer:
    """모델 응답 JSON → BreedAnswer. 형식이 틀리면 ValueError. '해당 없음' 칸은 빠진다."""
    data = json.loads(text) if isinstance(text, str) else text
    look = (data.get("look") or "").strip()
    if look not in LOOKS:
        raise ValueError(f"look: 보기에 없는 값 {look!r}")
    top = data.get("top")
    if not isinstance(top, list) or not top:
        raise ValueError("top 이 비었습니다")
    picks = []
    for i, t in enumerate(top[:3], start=1):
        name = normalize(t.get("breed"))
        if name is None:
            continue
        try:
            sim = int(t.get("similarity") or 0)
            rank = int(t.get("rank") or i)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"top[{i}] 숫자 형식 오류") from exc
        picks.append(BreedPick(rank, name, str(t.get("breed")).strip(), max(0, min(100, sim)), (t.get("reason") or "").strip()))
    return BreedAnswer((data.get("evidence") or "").strip(), look, tuple(picks))


@dataclass(frozen=True)
class BreedResult:
    """한 동물의 품종 판정. strong = 검색 태그로 쓸 품종, ranked = 두 회차 모두 3위 안에 든 품종 전체(평균 유사도순)."""

    strong: tuple[str, ...]
    ranked: tuple[tuple[str, int, float], ...]  # (품종, 평균 순위 순번 1~, 평균 유사도)


def decide(runs: Sequence[BreedAnswer], threshold: int = STRONG_SIMILARITY) -> BreedResult:
    """2회 답 → 강 신호 품종. 두 회차 모두 Top3 에 든 품종만 보고, 평균 유사도가 threshold 이상이면 강 신호."""
    if len(runs) < 2:
        return BreedResult((), ())
    sims = [r.similarities() for r in runs]
    common = set(sims[0])
    for s in sims[1:]:
        common &= set(s)
    ranks = {}
    for name in common:
        ranks[name] = sum(next(p.rank for p in r.top if p.breed == name) for r in runs) / len(runs)
    scored = sorted(common, key=lambda n: (-sum(s[n] for s in sims) / len(sims), ranks[n], n))
    ranked = tuple((n, i, sum(s[n] for s in sims) / len(sims)) for i, n in enumerate(scored, start=1))
    strong = tuple(n for n, _, avg in ranked if avg >= threshold)
    return BreedResult(strong, ranked)
