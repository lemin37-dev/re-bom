import json

import pytest

from rebom.tagging.trait_tags import (
    NO_TAG,
    display_tag,
    normalize_gold,
    OPTIONS,
    UNSURE,
    USER_PROMPT,
    average_score,
    majority,
    parse_answer,
    response_schema,
    score_to_option,
    to_score,
    unanimous,
)


def test_schema_puts_evidence_first_and_lists_options():
    s = response_schema()
    assert s["required"] == ["fur", "face"]
    for ax in ("fur", "face"):
        p = s["properties"][ax]
        assert p["propertyOrdering"] == ["evidence", "value"]
        assert p["properties"]["value"]["enum"] == list(OPTIONS[ax])


def test_prompt_has_eye_rule_and_no_breed():
    assert "머리(얼굴)만 보세요" in USER_PROMPT
    assert "귀가 선 것은" in USER_PROMPT
    assert "애매함" in USER_PROMPT
    assert "품종" not in USER_PROMPT


def test_parse_answer_ok_and_rejects_unknown():
    text = json.dumps({"fur": {"evidence": "짧은 털", "value": "매끈"}, "face": {"evidence": "둥근 얼굴", "value": "귀염"}})
    a = parse_answer(text)
    assert a["fur"].value == "매끈" and a["face"].evidence == "둥근 얼굴"
    with pytest.raises(ValueError):
        parse_answer(json.dumps({"fur": {"value": "긴 털"}, "face": {"value": "귀염"}}))


def test_scores():
    assert to_score("fur", "복슬복슬") == 1
    assert to_score("face", "늠름") == -1
    assert to_score("face", UNSURE) is None
    assert average_score("face", ["귀염", "귀염", "중간"]) == pytest.approx(2 / 3)
    assert average_score("face", [UNSURE, UNSURE]) is None
    assert average_score("fur", ["매끈", UNSURE]) == -1


def test_majority_unanimous_and_back_to_option():
    assert majority(["귀염", "귀염", "늠름"]) == "귀염"
    assert majority(["귀염", "늠름"]) is None
    assert unanimous(["매끈"] * 3) == "매끈"
    assert unanimous([UNSURE] * 3) is None
    assert unanimous(["매끈", "중간", "매끈"]) is None
    assert score_to_option("face", 0.67) == "귀염"
    assert score_to_option("face", 0.33) == "중간"
    assert score_to_option("face", -0.5) == "늠름"
    assert score_to_option("face", None) is None


def test_fur_two_level_with_no_tag():
    assert OPTIONS["fur"][:3] == ("복슬복슬", NO_TAG, "매끈")
    assert to_score("fur", NO_TAG) == 0
    assert normalize_gold("fur", "중간") == NO_TAG
    assert normalize_gold("face", "중간") == "중간"
    assert score_to_option("fur", 0.0) == NO_TAG


def test_display_tags():
    assert display_tag("fur", "매끈") == "짧은 털"
    assert display_tag("fur", "복슬복슬") == "복슬복슬"
    assert display_tag("fur", NO_TAG) is None
    assert display_tag("face", "중간") is None
    assert display_tag("face", "판단 어려움") is None


def test_v5_face_axis_cute_brave():
    from rebom.tagging.trait_tags import PROMPT_VERSION

    assert PROMPT_VERSION.startswith("trait-vision-v")
    assert "귀염" in OPTIONS["face"]
    assert "순둥" not in OPTIONS["face"]
    assert "자세" in USER_PROMPT
    assert display_tag("face", "귀염") == "귀염"


def test_v6_face_ignores_age_and_body():
    from rebom.tagging.trait_tags import CRITERIA, PROMPT_VERSION

    assert PROMPT_VERSION == "trait-vision-v6"
    assert "어른스럽고" not in CRITERIA["face"]["늠름"]
    assert "두 가지 이상" in CRITERIA["face"]["늠름"]
    assert "다 큰 개라는 것만으로 늠름이 아닙니다" in USER_PROMPT
    assert "몸 크기" in USER_PROMPT
