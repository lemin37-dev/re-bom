from datetime import date

import pytest

from rebom.serving.derive import age_label, age_years, sido, sigungu, weight_kg

TODAY = date(2026, 11, 2)


@pytest.mark.parametrize(
    "text,years,label",
    [
        ("2026(60일미만)(년생)", 0, "60일 미만"),
        ("2026(년생)", 0, "1살 미만"),
        ("2023(년생)", 3, "3살"),
        (None, None, None),
        ("미상", None, None),
    ],
)
def test_age(text, years, label):
    assert age_years(text, TODAY) == years
    assert age_label(text, TODAY) == label


@pytest.mark.parametrize(
    "text,kg",
    [
        ("13(Kg)", 13.0),
        ("0.8~0.9(Kg)", 0.85),
        ("330g(Kg)", 0.33),
        ("10.00(Kg)", 10.0),
        ("3~3.5(Kg)", 3.25),
        (None, None),
        ("(Kg)", None),
    ],
)
def test_weight(text, kg):
    assert weight_kg(text) == kg


def test_region_parts():
    assert sido("경기도 수원시") == "경기도"
    assert sigungu("경기도 수원시") == "수원시"
    assert sido(None) is None and sigungu("서울특별시") is None
