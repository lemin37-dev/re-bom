import json

import pytest

from rebom.tagging.trait_tags import (
    NO_TAG,
    display_tag,
    normalize_gold,
    FACE_FEATURES,
    OPTIONS,
    face_from_features,
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
    p = s["properties"]["fur"]
    assert p["propertyOrdering"] == ["evidence", "value"]
    assert p["properties"]["value"]["enum"] == list(OPTIONS["fur"])
    face = s["properties"]["face"]
    assert set(face["required"]) == set(FACE_FEATURES)
    for k, opts in FACE_FEATURES.items():
        assert face["properties"][k]["enum"] == list(opts)


def test_prompt_has_eye_rule_and_no_breed():
    assert "머리(얼굴)만 보세요" in USER_PROMPT
    assert "expression 칸에서만" in USER_PROMPT
    assert "애매함" in USER_PROMPT
    assert "품종" not in USER_PROMPT


def test_parse_answer_ok_and_rejects_unknown():
    face = {"puppy": "아니오", "ears": "늘어짐", "expression": "순함", "muzzle": "짧음", "eyes": "애매함"}
    text = json.dumps({"fur": {"evidence": "짧은 털", "value": "매끈"}, "face": face})
    a = parse_answer(text)
    assert a["fur"].value == "매끈"
    assert a["face"].value == "귀염" and a["face"].features == face
    assert "주둥이 짧음" in a["face"].evidence
    with pytest.raises(ValueError):
        parse_answer(json.dumps({"fur": {"value": "긴 털"}, "face": face}))
    with pytest.raises(ValueError):
        parse_answer(json.dumps({"fur": {"value": "매끈"}, "face": {**face, "muzzle": "보통"}}))


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
    assert display_tag("face", "귀염") == "귀염"


def test_v8_element_tags_and_combined_impression():
    from rebom.tagging.trait_tags import PROMPT_VERSION, agreed_features, element_tags

    assert PROMPT_VERSION == "trait-vision-v8"
    base = {"puppy": "아니오", "ears": "반쯤 섬", "expression": "보통", "muzzle": "애매함", "eyes": "애매함"}
    assert element_tags(base) == {"ears": "반쯤 섬"}
    assert face_from_features(base) == "중간"
    # 어린 강아지는 다른 요소와 상관없이 귀염
    assert face_from_features({**base, "puppy": "예", "muzzle": "김", "eyes": "날카로움"}) == "귀염"
    # 늠름 쪽 2개, 귀염 쪽 0개 → 늠름
    brave = {**base, "muzzle": "김", "eyes": "날카로움"}
    assert face_from_features(brave) == "늠름"
    assert element_tags(brave) == {"ears": "반쯤 섬", "muzzle": "김", "eyes": "날카로움"}
    # 늠름 2 대 귀염 1 → 차이 1 이라 태그 없음
    assert face_from_features({**brave, "ears": "늘어짐"}) == "중간"
    assert face_from_features({**base, "ears": "늘어짐", "expression": "순함", "muzzle": "짧음"}) == "귀염"
    assert face_from_features({**base, "ears": "안 보임", "expression": "안 보임", "muzzle": "안 보임"}) == UNSURE
    # 회차마다 다른 칸은 버림
    r2 = {**brave, "eyes": "둥글함"}
    agreed = agreed_features([brave, r2])
    assert "eyes" not in agreed and agreed["muzzle"] == "김"
    assert face_from_features(agreed) == "중간"
    assert "다 큰 개라는 것만으로" in USER_PROMPT
    assert "몸 크기" in USER_PROMPT
