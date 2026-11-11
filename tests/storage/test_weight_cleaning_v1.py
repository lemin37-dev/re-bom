"""Tests for RE:봄 Silver weight cleaning v1 (uncertain values stay NULL)."""

from rebom.storage.cleaning.weight import parse_weight
from rebom.storage.silver.transform_notice import transform_notice


def test_normal_and_format_cleanup():
    assert parse_weight("2.5(Kg)", "개") == (2.5, 2.5, "parsed_single")
    assert parse_weight("2,5(Kg)", "개") == (2.5, 2.5, "parsed_single")
    assert parse_weight("12..00(Kg)", "고양이") == (12.0, 12.0, "parsed_single")
    assert parse_weight("1.8~2.0(Kg)", "개") == (1.8, 2.0, "parsed_range")
    assert parse_weight("-(Kg)", "개") == (None, None, "missing")


def test_small_animal_outliers_and_uncertain_units():
    for species in ("개", "고양이", "[개]", "[고양이]"):
        for weight in ("205(Kg)", "800(Kg)", "2500(Kg)", "99~101(Kg)"):
            assert parse_weight(weight, species) == (None, None, "over_100kg_unverified")
    assert parse_weight("330g(Kg)", "고양이") == (0.33, 0.33, "parsed_grams")
    assert parse_weight("18g(Kg)", "개") == (0.018, 0.018, "parsed_grams")
    assert parse_weight("12...00(Kg)", "개") == (None, None, "ambiguous")
    assert parse_weight("100(Kg)", "개") == (100.0, 100.0, "parsed_single")


def test_other_species_not_subject_to_dog_cat_cap():
    assert parse_weight("2500(Kg)", "소") == (2500.0, 2500.0, "parsed_single")
    assert parse_weight("205(Kg)", "기타") == (205.0, 205.0, "parsed_single")


def test_notice_integration_and_original_preserved():
    raw = {"desertionNo": "T-1", "upKindNm": "개", "weight": "2500(Kg)"}
    transformed = transform_notice(raw)
    assert transformed["weight_raw"] == "2500(Kg)"
    assert transformed["weight_min_kg"] is None
    assert transformed["weight_max_kg"] is None
    assert transformed["weight_parse_status"] == "over_100kg_unverified"
