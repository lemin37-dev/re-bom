"""검색 화면의 사진 태그: 큰 범주 → 상세 태그 사전, 태그 조건 해석, 공고별 태그 읽기.

태그 코드·이름은 사진 태거(rebom.tagging.tag_codes)와 같은 사전을 쓴다. 화면에 보이는 태그(displayable)만 고를 수
있게 하며, 품종 태그는 화면에 보이지 않는 태그라 빠진다(docs/data_contract.md 13.2, 11/17 결정).

검색 조건: 같은 범주 안에서 고른 태그는 하나라도 맞으면(OR), 범주끼리는 모두 맞아야(AND) 통과한다.
예) 귀: 선 귀·반쯤 선 귀 + 털: 복슬복슬 → (선 귀 또는 반쯤 선 귀) 이면서 복슬복슬.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

from rebom.tagging import tag_codes

# 화면에 보이는 순서. 키는 태그 코드 앞부분(fur.fluffy → fur)
CATEGORIES: tuple[tuple[str, str], ...] = (("fur", "털"), ("ear", "귀 모양"), ("eye", "눈매"))
CATEGORY_LABELS = dict(CATEGORIES)
RECOMMENDED_COUNT = 4

TagGroups = tuple[frozenset[str], ...]  # 범주마다 고른 태그 묶음


def category_of(code: str) -> str:
    return code.split(".", 1)[0]


def selectable() -> list[tag_codes.TagDef]:
    """고를 수 있는 태그(검색·화면 모두 쓰는 태그), 사전 순서대로."""
    return [
        t for t in tag_codes.TAGS.values()
        if t.searchable and t.displayable and category_of(t.code) in CATEGORY_LABELS
    ]


def parse_tags(text: str | None) -> TagGroups:
    """'ear.drop,fur.fluffy' → 범주별 묶음. 모르는 코드는 ValueError(코드를 알림)."""
    codes = [c.strip() for c in (text or "").split(",") if c.strip()]
    known = {t.code for t in selectable()}
    unknown = [c for c in codes if c not in known]
    if unknown:
        raise ValueError(f"모르는 태그: {', '.join(unknown)}")
    groups: dict[str, set[str]] = {}
    for code in codes:
        groups.setdefault(category_of(code), set()).add(code)
    return tuple(frozenset(groups[key]) for key, _ in CATEGORIES if key in groups)


def matches(animal_tags: frozenset[str], groups: TagGroups) -> bool:
    return all(animal_tags & group for group in groups)


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


def catalog(counts: Mapping[str, int]) -> dict:
    """범주별 상세 태그와 추천 태그. counts 는 태그별 동물 수(보호중)."""
    tags = selectable()
    categories = [
        {
            "key": key,
            "label": label,
            "tags": [{"code": t.code, "label": t.label, "count": counts.get(t.code, 0)}
                     for t in tags if category_of(t.code) == key],
        }
        for key, label in CATEGORIES
    ]
    order = {t.code: i for i, t in enumerate(tags)}
    ranked = sorted((t.code for t in tags if counts.get(t.code, 0) > 0), key=lambda c: (-counts[c], order[c]))
    return {"categories": categories, "recommended": ranked[:RECOMMENDED_COUNT]}


def count_tags(tag_sets: Iterable[frozenset[str]]) -> Counter:
    return Counter(code for tags in tag_sets for code in tags)
