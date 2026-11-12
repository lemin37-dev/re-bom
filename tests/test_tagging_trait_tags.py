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
    assert "눈매" in USER_PROMPT
    assert "귀가 서 있다는 것만으로 늠름을 고르지" in USER_PROMPT
    assert "동그랗다는 것만으로 순둥을 고르지" in USER_PROMPT
    assert "애매함" in USER_PROMPT
    assert "품종" not in USER_PROMPT


def test_parse_answer_ok_and_rejects_unknown():
    text = json.dumps({"fur": {"evidence": "짧은 털", "value": "매끈"}, "face": {"evidence": "처진 눈", "value": "순둥"}})
    a = parse_answer(text)
    assert a["fur"].value == "매끈" and a["face"].evidence == "처진 눈"
    with pytest.raises(ValueError):
        parse_answer(json.dumps({"fur": {"value": "긴 털"}, "face": {"value": "순둥"}}))


def test_scores():
    assert to_score("fur", "복슬복슬") == 1
    assert to_score("face", "늠름") == -1
    assert to_score("face", UNSURE) is None
    assert average_score("face", ["순둥", "순둥", "중간"]) == pytest.approx(2 / 3)
    assert average_score("face", [UNSURE, UNSURE]) is None
    assert average_score("fur", ["매끈", UNSURE]) == -1


def test_majority_unanimous_and_back_to_option():
    assert majority(["순둥", "순둥", "늠름"]) == "순둥"
    assert majority(["순둥", "늠름"]) is None
    assert unanimous(["매끈"] * 3) == "매끈"
    assert unanimous([UNSURE] * 3) is None
    assert unanimous(["매끈", "중간", "매끈"]) is None
    assert score_to_option("face", 0.67) == "순둥"
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
