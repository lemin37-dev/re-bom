"""검색 화면의 사진 태그: 종(강아지·고양이)별 태그 세트 → 큰 범주 → 상세 태그, 태그 조건 해석, 공고별 태그 읽기.

화면은 강아지·고양이를 먼저 고르고 그 종의 태그 세트를 보여 준다. 고를 수 있는 태그와 범주는 아래 TAG_SETS 표만 따른다.
태그 코드·이름은 사진 태거(rebom.tagging.tag_codes)와 같은 사전을 쓴다. 품종 태그는 화면에 보이지 않는 태그라 표에
넣지 않는다(docs/data_contract.md 13.2, 11/17 결정). 고양이 태그는 고양이 특성에 맞게 따로 만들 예정이다. 그때 tag_codes 에
코드를 더하고(강아지 코드와 겹치지 않게) TAG_SETS["cat"] 에 범주를 채우면 된다.

태그는 거르는 조건이 아니라 순서다. 고른 범주마다(범주 안은 하나라도 맞으면 맞음) 아이의 태그를 맞음·반대·정보 없음으로
보고, 아래 순서로 앞에 둔다. 태그가 없거나 다른 아이도 결과에 남는다(모델 태그가 틀리거나 비어 있을 수 있어서).
  all     고른 범주가 모두 맞음
  some    일부 범주만 맞음
  unknown 고른 범주의 태그가 없음 (태그 없는 아이, 다른 축종 포함)
  other   태그는 있는데 고른 것과 다름
exclusive 범주(강아지 털: 복슬복슬·짧은 털은 서로 반대)는 고른 것과 다른 태그가 붙은 아이를 결과에서 뺀다.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rebom.tagging import tag_codes

Species = Literal["dog", "cat"]


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    codes: tuple[str, ...]  # 화면에 보이는 순서
    exclusive: bool = False  # True 면 고른 것과 다른 태그가 붙은 아이를 결과에서 뺀다


# 종별 태그 세트. 화면 순서대로
TAG_SETS: dict[Species, tuple[Category, ...]] = {
    "dog": (
        Category("fur", "털", ("fur.fluffy", "fur.short"), exclusive=True),
        Category("ear", "귀 모양", ("ear.drop", "ear.semi", "ear.prick")),
        Category("eye", "눈매", ("eye.round", "eye.sharp")),
    ),
    "cat": (),  # 고양이 태그 세트는 준비 중
}
RECOMMENDED_COUNT = 4

TagMatch = Literal["all", "some", "unknown", "other"]
MATCH_RANK: dict[str, int] = {"all": 0, "some": 1, "unknown": 2, "other": 3}

TagGroups = tuple[frozenset[str], ...]  # 범주마다 고른 태그 묶음


_CATEGORIES: tuple[Category, ...] = tuple(c for cats in TAG_SETS.values() for c in cats)
_CATEGORY_OF: dict[str, Category] = {code: c for c in _CATEGORIES for code in c.codes}


def selectable() -> list[tag_codes.TagDef]:
    """고를 수 있는 태그 (모든 종), 표 순서대로."""
    return [tag_codes.TAGS[code] for c in _CATEGORIES for code in c.codes]


def parse_tags(text: str | None) -> TagGroups:
    """'ear.drop,fur.fluffy' → 범주별 묶음. 모르는 코드는 ValueError(코드를 알림)."""
    codes = [c.strip() for c in (text or "").split(",") if c.strip()]
    known = {t.code for t in selectable()}
    unknown = [c for c in codes if c not in known]
    if unknown:
        raise ValueError(f"모르는 태그: {', '.join(unknown)}")
    groups: dict[Category, set[str]] = {}
    for code in codes:
        groups.setdefault(_CATEGORY_OF[code], set()).add(code)
    return tuple(frozenset(groups[c]) for c in _CATEGORIES if c in groups)


def tag_match(animal_tags: frozenset[str], groups: TagGroups) -> TagMatch | None:
    """고른 태그에 대한 이 아이의 단계. 털이 반대라서 결과에서 뺄 아이는 None."""
    matched = conflicted = 0
    for group in groups:
        category = _CATEGORY_OF[next(iter(group))]
        have = {t for t in animal_tags if _CATEGORY_OF.get(t) is category}
        if have & group:
            matched += 1
        elif have:
            if category.exclusive:
                return None
            conflicted += 1
    if matched == len(groups):
        return "all"
    if matched:
        return "some"
    return "other" if conflicted else "unknown"


def tag_detail(animal_tags: frozenset[str], groups: TagGroups) -> list[dict]:
    """범주마다 고른 태그·이 아이 태그·판정(match 맞음 / other 다름 / unknown 정보 없음). dev 모드 순위 근거용."""
    out = []
    for group in groups:
        category = _CATEGORY_OF[next(iter(group))]
        have = {t for t in animal_tags if _CATEGORY_OF.get(t) is category}
        result = "match" if have & group else ("other" if have else "unknown")
        out.append({
            "category": category.key, "label": category.label, "exclusive": category.exclusive,
            "picked": [c for c in category.codes if c in group], "have": [c for c in category.codes if c in have],
            "result": result,
        })
    return out


def load_sample_tags(path: Path) -> dict[str, frozenset[str]]:
    """data/sample/photo_tags.json → 공고번호별 태그. 파일이 없으면 빈 사전(태그 없이 동작)."""
    if not path.exists():
        return {}
    items = json.loads(path.read_text(encoding="utf-8")).get("items") or []
    known = {t.code for t in selectable()}
    return {str(i["desertionNo"]): frozenset(c for c in i.get("tags") or () if c in known) for i in items}


def chips(codes: Iterable[str]) -> list[dict]:
    """공고 카드에 붙일 태그 칩(code, label), 사전 순서대로. 고를 수 없는 태그는 빠진다."""
    have = set(codes)
    return [{"code": t.code, "label": t.label} for t in selectable() if t.code in have]


def catalog(species: Species, counts: Mapping[str, int]) -> dict:
    """그 종의 범주별 상세 태그와 추천 태그. counts 는 태그별 동물 수(보호중). 태그 세트가 없는 종은 범주가 비어 있다."""
    cats = TAG_SETS[species]
    categories = [
        {
            "key": c.key,
            "label": c.label,
            "tags": [{"code": code, "label": tag_codes.TAGS[code].label, "count": counts.get(code, 0)} for code in c.codes],
        }
        for c in cats
    ]
    codes = [code for c in cats for code in c.codes]
    ranked = sorted((code for code in codes if counts.get(code, 0) > 0), key=lambda c: (-counts[c], codes.index(c)))
    return {"species": species, "categories": categories, "recommended": ranked[:RECOMMENDED_COUNT]}


def count_tags(tag_sets: Iterable[frozenset[str]]) -> Counter:
    return Counter(code for tags in tag_sets for code in tags)
