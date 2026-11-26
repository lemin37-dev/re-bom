import pytest

from rebom.storage.silver.transform_notice import transform_notice


@pytest.mark.parametrize(
    "weight, expected",
    [
        ("9.9(Kg)", "small"),
        ("10(Kg)", "medium"),
        ("10.1(Kg)", "medium"),
        ("24.9(Kg)", "medium"),
        ("25(Kg)", "large"),
        ("25.1(Kg)", "large"),
        ("8~10(Kg)", "medium"),
        ("20~25(Kg)", "large"),
        (None, None),
    ],
)
def test_dog_size_boundary(weight, expected):
    raw = {
        "desertionNo": "TEST001",
        "upKindCd": "417000",
        "upKindNm": "개",
        "weight": weight,
    }

    assert transform_notice(raw)["size"] == expected


def test_cat_size_is_none():
    raw = {
        "desertionNo": "TEST002",
        "upKindCd": "422400",
        "upKindNm": "고양이",
        "weight": "5(Kg)",
    }

    assert transform_notice(raw)["size"] is None
