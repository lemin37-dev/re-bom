"""털·귀·눈매·품종 판정 규칙: 2회 답이 같을 때만 태그, 품종은 강 신호(두 회차 Top3 + 평균 90 이상)만 검색."""
import json

import pytest

from rebom.tagging import breed_tags, tag_codes, trait_tags


def trait(fur="매끈", ears="섬", eyes="날카로움", **face):
    feats = {"puppy": "아니오", "ears": ears, "expression": "보통", "muzzle": "애매함", "eyes": eyes, **face}
    return trait_tags.parse_answer({"fur": {"evidence": "e", "value": fur}, "face": feats})


def test_trait_tags_need_both_runs_to_agree():
    assert trait_tags.decide_tags([trait(), trait()]) == ["fur.short", "ear.prick", "eye.sharp"]
    assert trait_tags.decide_tags([trait(fur="복슬복슬"), trait(fur="매끈")]) == ["ear.prick", "eye.sharp"]
    assert trait_tags.decide_tags([trait(ears="섬"), trait(ears="반쯤 섬")]) == ["fur.short", "eye.sharp"]
    assert trait_tags.decide_tags([trait()]) == []


@pytest.mark.parametrize("fur,ears,eyes", [("애매함", "안 보임", "애매함"), ("판단 어려움", "안 보임", "안 보임")])
def test_middle_and_hidden_options_give_no_tag(fur, ears, eyes):
    a = trait(fur=fur, ears=ears, eyes=eyes)
    assert trait_tags.decide_tags([a, a]) == []


def test_semi_ear_and_round_eye_codes():
    a = trait(fur="복슬복슬", ears="반쯤 섬", eyes="둥글함")
    assert trait_tags.decide_tags([a, a]) == ["fur.fluffy", "ear.semi", "eye.round"]


def test_impression_is_never_a_tag():
    a = trait(puppy="예", expression="순함", muzzle="짧음")
    codes = trait_tags.decide_tags([a, a])
    assert all(not c.startswith(("face", "impression")) for c in codes)
    assert all(tag_codes.get(c).label != "귀염" for c in codes)


def test_trait_parse_rejects_unknown_option():
    with pytest.raises(ValueError):
        trait_tags.parse_answer({"fur": {"evidence": "", "value": "중간"}, "face": {}})


def test_trait_prompt_does_not_send_notice_breed():
    assert "kindNm" not in trait_tags.USER_PROMPT and "품종명" not in trait_tags.USER_PROMPT
    assert trait_tags.PROMPT_VERSION == "trait-vision-v10"


def breed(*top, look="믹스로 보임"):
    return breed_tags.parse_answer({
        "evidence": "e", "look": look,
        "top": [{"rank": i, "breed": b, "similarity": s, "reason": ""} for i, (b, s) in enumerate(top, start=1)],
    })


def test_normalize_aliases_to_candidate_names():
    assert breed_tags.normalize("진돗개") == "진도견"
    assert breed_tags.normalize("Shiba") == "Shiba"
    assert breed_tags.normalize("미니어처 푸들") == "푸들"
    assert breed_tags.normalize("해당 없음") is None
    assert len(breed_tags.load_candidates()) == 62


def test_strong_signal_needs_both_runs_and_avg_90():
    r1 = breed(("진돗개", 92), ("시바견", 70), ("해당 없음", 0))
    r2 = breed(("진도견", 88), ("스피츠", 60), ("시바", 55))
    res = breed_tags.decide([r1, r2])
    assert res.strong == ("진도견",)  # (92+88)/2 = 90
    assert [n for n, _, _ in res.ranked] == ["진도견", "시바"]


def test_breed_in_only_one_run_is_not_strong():
    r1 = breed(("푸들", 95), ("말티즈", 60), ("비숑", 40))
    r2 = breed(("말티즈", 91), ("비숑 프리제", 50), ("시츄", 30))
    res = breed_tags.decide([r1, r2])
    assert res.strong == ()
    assert {n for n, _, _ in res.ranked} == {"말티즈", "비숑 프리제"}


def test_breed_tag_is_hidden_on_screen():
    t = tag_codes.breed_tag("진도견")
    assert t.code == "breed.진도견" and t.searchable and not t.displayable


def test_breed_prompt_has_62_candidates_and_no_feature_table():
    p = breed_tags.build_prompt()
    assert "진도견" in p and "특징표" not in p
    assert json.dumps(breed_tags.response_schema())  # 직렬화 가능
