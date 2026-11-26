"""화면 표시·필터용 파생 값 (sample 모드 임시).

원본 나이·체중은 자유 텍스트라 정제 단계(src/rebom/cleaning/)에서 숫자 컬럼으로 만들 예정이다.
db 모드가 붙으면 정제된 컬럼을 쓰고, 이 모듈은 sample 모드에서만 쓴다.
"""
from __future__ import annotations

import re
from datetime import date

from rebom.storage.cleaning.weight import parse_weight

DOG_CODE = "417000"
_YEAR = re.compile(r"(\d{4})")


def birth_year(age_text: str | None) -> int | None:
    """'2024(년생)' → 2024. 못 읽으면 None."""
    m = _YEAR.search(age_text or "")
    return int(m.group(1)) if m else None


def age_years(age_text: str | None, today: date) -> int | None:
    """출생연도 기준 추정 나이(만 나이 아님). '60일미만' 은 0."""
    if age_text and "60일미만" in age_text:
        return 0
    year = birth_year(age_text)
    if year is None:
        return None
    return max(0, today.year - year)


def age_label(age_text: str | None, today: date) -> str | None:
    if age_text and "60일미만" in age_text:
        return "60일 미만"
    years = age_years(age_text, today)
    if years is None:
        return None
    return "1살 미만" if years == 0 else f"{years}살"


def weight_kg(weight_text: str | None, species_name: str | None = None) -> float | None:
    """Silver weight_kg 와 같은 값: '13(Kg)' → 13, '0.8~0.9(Kg)' → 0.9 (범위는 큰 값), '330g(Kg)' → 0.33.

    단위가 애매하거나 개·고양이 100kg 초과는 None (storage.cleaning.weight.parse_weight).
    """
    kg = parse_weight(weight_text, species_name)[1]
    return None if kg is None else round(kg, 2)


def size(kg: float | None, species_code: str | None) -> str | None:
    """크기 (개만): 10kg 미만 small · 25kg 미만 medium · 이상 large. 화면 크기 필터(weight_lt=10 등)와 같은 경계."""
    if kg is None or species_code != DOG_CODE:
        return None
    return "small" if kg < 10 else "medium" if kg < 25 else "large"


def sido(addr: str | None) -> str | None:
    """'경기도 수원시' → '경기도'."""
    parts = (addr or "").split()
    return parts[0] if parts else None


def sigungu(addr: str | None) -> str | None:
    parts = (addr or "").split()
    return parts[1] if len(parts) > 1 else None
