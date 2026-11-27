from datetime import date

import pytest

from rebom.serving.derive import age_label, age_years, birth_year, sido, sigungu, size, weight_kg

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
        ("0.8~0.9(Kg)", 0.9),
        ("330g(Kg)", 0.33),
        ("10.00(Kg)", 10.0),
        ("3~3.5(Kg)", 3.5),
        ("1,5(Kg)", 1.5),
        ("3kg(Kg)", 3.0),
        (None, None),
        ("(Kg)", None),
    ],
)
def test_weight(text, kg):
    assert weight_kg(text) == kg


def test_weight_drops_unverified_over_100kg_dog():
    assert weight_kg("150(Kg)", "개") is None


@pytest.mark.parametrize(
    "kg,code,expected",
    [
        (9.9, "417000", "small"),
        (10.0, "417000", "medium"),
        (24.9, "417000", "medium"),
        (25.0, "417000", "large"),
        (4.0, "422400", None),  # 고양이는 크기 없음
        (None, "417000", None),
    ],
)
def test_size(kg, code, expected):
    assert size(kg, code) == expected


def test_birth_year():
    assert birth_year("2022(년생)") == 2022
    assert birth_year(None) is None


def test_region_parts():
    assert sido("경기도 수원시") == "경기도"
    assert sigungu("경기도 수원시") == "수원시"
    assert sido(None) is None and sigungu("서울특별시") is None
