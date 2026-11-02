"""화면 표시·필터용 파생 값 (sample 모드 임시).

원본 나이·체중은 자유 텍스트라 정제 단계(src/rebom/cleaning/)에서 숫자 컬럼으로 만들 예정이다.
db 모드가 붙으면 정제된 컬럼을 쓰고, 이 모듈은 sample 모드에서만 쓴다.
"""
from __future__ import annotations

import re
from datetime import date

_YEAR = re.compile(r"(\d{4})")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


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


def weight_kg(weight_text: str | None) -> float | None:
    """'13(Kg)' → 13, '0.8~0.9(Kg)' → 0.85 (범위는 평균), '330g(Kg)' → 0.33."""
    text = (weight_text or "").replace(",", ".")
    nums = [float(n) for n in _NUMBER.findall(text)]
    if not nums:
        return None
    value = sum(nums[:2]) / len(nums[:2])
    head = text.split("(")[0].lower()
    if head.endswith("g") and not head.endswith("kg"):
        value /= 1000
    return round(value, 2)


def sido(addr: str | None) -> str | None:
    """'경기도 수원시' → '경기도'."""
    parts = (addr or "").split()
    return parts[0] if parts else None


def sigungu(addr: str | None) -> str | None:
    parts = (addr or "").split()
    return parts[1] if len(parts) > 1 else None
