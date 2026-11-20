from datetime import date

import pytest

from rebom.serving.derive import age_label, age_years, sido, sigungu, size, weight_kg

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
        ("0.8~0.9(Kg)", 0.9),  # Silver 와 같이 범위는 큰 값
        ("330g(Kg)", 0.33),
        ("10.00(Kg)", 10.0),
        ("3~3.5(Kg)", 3.5),
        (None, None),
        ("(Kg)", None),
    ],
)
def test_weight(text, kg):
    assert weight_kg(text) == kg


@pytest.mark.parametrize(
    "text,code,name,want",
    [
        ("9.9(Kg)", "417000", "개", "small"),
        ("10(Kg)", "417000", "개", "medium"),  # 11-20: 10kg 미만 소형
        ("24.7(Kg)", "417000", "개", "medium"),
        ("25(Kg)", "417000", "개", "large"),
        ("5(Kg)", "422400", "고양이", None),  # 개만
        (None, "417000", "개", None),
    ],
)
def test_size_same_as_silver(text, code, name, want):
    from rebom.storage.silver.transform_notice import transform_notice

    assert size(text, code, name) == want
    silver = transform_notice({"desertionNo": "T", "upKindCd": code, "upKindNm": name, "weight": text})
    assert silver["size"] == want and silver["weight_kg"] == weight_kg(text, name)


def test_region_parts():
    assert sido("경기도 수원시") == "경기도"
    assert sigungu("경기도 수원시") == "수원시"
    assert sido(None) is None and sigungu("서울특별시") is None
