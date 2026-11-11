from rebom.storage.silver.transform_notice import transform_notice


def test_mix_breed():
    result = transform_notice({
        "desertionNo": "TEST-001",
        "kindCd": "000114",
        "kindNm": "믹스견",
        "age": "2026(60일미만)(년생)",
    })
    assert result["is_mix"] is True
    assert result["u60_at_notice"] is True


def test_other_breed():
    result = transform_notice({
        "desertionNo": "TEST-002",
        "kindCd": "000115",
        "kindNm": "기타",
        "age": "2022(년생)",
    })
    assert result["is_mix"] is True
    assert result["u60_at_notice"] is False


def test_pure_breed():
    result = transform_notice({
        "desertionNo": "TEST-003",
        "kindCd": "000128",
        "kindNm": "푸들",
        "age": "2021(년생)",
    })
    assert result["is_mix"] is False
    assert result["u60_at_notice"] is False

import pytest


@pytest.mark.parametrize(
    "weight,expected_weight,expected_size",
    [
        ("10(Kg)", 10.0, "small"),
        ("10.1(Kg)", 10.1, "medium"),
        ("24.9(Kg)", 24.9, "medium"),
        ("25(Kg)", 25.0, "large"),
        ("1.8~2.0(Kg)", 2.0, "small"),
        ("2500(Kg)", None, None),
    ],
)
def test_weight_kg_and_size(weight, expected_weight, expected_size):
    result = transform_notice({
        "desertionNo": "TEST-WEIGHT",
        "upKindCd": "417000",
        "upKindNm": "\uac1c",
        "weight": weight,
    })
    assert result["weight_kg"] == expected_weight
    assert result["size"] == expected_size


def test_size_not_assigned_to_cat():
    result = transform_notice({
        "desertionNo": "TEST-CAT",
        "upKindCd": "422400",
        "upKindNm": "\uace0\uc591\uc774",
        "weight": "4(Kg)",
    })
    assert result["weight_kg"] == 4.0
    assert result["size"] is None

def test_info_text_and_rfid():
    result = transform_notice({
        "desertionNo": "TEST-INFO",
        "sfeSoci": "친화적",
        "sfeHealth": "건강 양호",
        "specialMark": "사람을 좋아함",
        "rfidCd": "123456789",
    })
    assert result["info_text"] == "친화적 건강 양호 사람을 좋아함"
    assert result["rfid_yn"] is True


def test_missing_info_and_rfid():
    result = transform_notice({
        "desertionNo": "TEST-EMPTY",
        "sfeSoci": "",
        "sfeHealth": None,
        "specialMark": " ",
        "rfidCd": None,
    })
    assert result["info_text"] is None
    assert result["rfid_yn"] is False


def test_notice_date_from_notice_start():
    from datetime import date

    result = transform_notice({
        "desertionNo": "TEST-DATE",
        "noticeSdt": "20261111",
    })
    assert result["notice_date"] == date(2026, 11, 11)
    assert result["notice_date"] == result["notice_start_date"]


def test_explicit_grams_converted_to_kg():
    result = transform_notice({
        "desertionNo": "TEST-GRAMS",
        "upKindNm": "\uac1c",
        "upKindCd": "417000",
        "weight": "330g(Kg)",
    })
    assert result["weight_raw"] == "330g(Kg)"
    assert result["weight_kg"] == 0.33
    assert result["weight_parse_status"] == "parsed_grams"
    assert result["size"] == "small"
