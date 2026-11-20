"""text_card.py 가 기준 구현(text_card_reference.py)과 글자까지 같은 카드를 내는지."""
import importlib.util
from pathlib import Path

import pytest

from rebom.embedding import text_card as tc

REF_PATH = Path(__file__).resolve().parents[1] / "src" / "rebom" / "embedding" / "text_card_reference.py"


@pytest.fixture(scope="module")
def ref():
    if not REF_PATH.exists():
        pytest.skip("text_card_reference.py 없음")
    spec = importlib.util.spec_from_file_location("text_card_reference", REF_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ROWS = [
    {"kindNm": "믹스견", "age": "2026(60일미만)(년생)", "sexCd": "M", "neuterYn": "N", "weight": "2.3(Kg)",
     "colorCd": "갈색&검정&흰색", "specialMark": "전염병감염위험, 케어필요"},
    {"kindNm": "진도견", "age": "2021(년생)", "sexCd": "F", "neuterYn": "Y", "weight": "3~4(Kg)",
     "colorCd": "흑갈색", "specialMark": "관리번호 12-345 순함 문의 010-1234-5678"},
    {"kindNm": "코리안숏헤어", "age": "", "sexCd": "Q", "neuterYn": "U", "weight": "500g(60일미만)",
     "colorCd": "호피", "specialMark": None},
    {"kindNm": None, "age": None, "weight": "1,5(Kg)", "colorCd": "어두운색", "sfeSoci": "사람 좋아함",
     "specialMark": "  여러   칸  공백  "},
    {"kindNm": "믹스견", "weight": "30(Kg)", "colorCd": None},
]
TAGS = [("털 길이", "단모"), ("귀", "확인불가"), ("꼬리", ""), ("눈", None), ("체형", "날씬")]


@pytest.mark.parametrize("template", ["A1", "A2"])
@pytest.mark.parametrize("row", ROWS)
def test_same_as_reference(ref, row, template):
    assert tc.build_card(row, template, TAGS) == ref.build_card(row, template, TAGS)


def test_known_a1_card():
    want = ("믹스견, 2026년생, 수컷, 중성화 안 함, 2.3kg 소형, 털색 흰색계열·검정계열·갈색계열, "
            "(공고 털색 갈색&검정&흰색). 특징: 전염병감염위험, 케어필요")
    assert tc.build_card(ROWS[0], "A1") == want


def test_from_silver_maps_names_and_drops_others():
    silver = {"desertion_no": "1", "breed_name_raw": "믹스견", "breed_full_name_raw": "[개] 믹스견",
              "age_raw": "2026(년생)", "sex_code": "M", "neuter_code": "N", "weight_raw": "2.3(Kg)",
              "color_raw": "흰색", "special_mark": "순함", "color_tag": ["틀린값"], "size": "large",
              "happen_place": "어딘가", "care_owner_nm": "x", "care_tel": "010-0000-0000", "sfe_soci": "온순"}
    api = tc.from_silver(silver)
    assert api == {"kindNm": "믹스견", "age": "2026(년생)", "sexCd": "M", "neuterYn": "N",
                   "weight": "2.3(Kg)", "colorCd": "흰색", "specialMark": "순함"}
    text, sha = tc.card_from_silver(silver, "A1")
    assert text == "믹스견, 2026년생, 수컷, 중성화 안 함, 2.3kg 소형, 털색 흰색계열, (공고 털색 흰색). 특징: 순함"
    assert "대형" not in text and "틀린값" not in text  # Silver color_tag·size 는 안 씀
    assert sha == tc.text_sha256(text) and len(sha) == 64


def test_hash_ignores_whitespace_only():
    assert tc.text_sha256("a  b\n c") == tc.text_sha256("a b c")
    assert tc.text_sha256("a b c") != tc.text_sha256("a b d")


def test_bad_template():
    with pytest.raises(ValueError):
        tc.build_card(ROWS[0], "B1")


REF2_PATH = REF_PATH.with_name("text_card_reference2.py")


@pytest.fixture(scope="module")
def ref2():
    if not REF2_PATH.exists():
        pytest.skip("text_card_reference2.py 없음")
    spec = importlib.util.spec_from_file_location("text_card_reference2", REF2_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("row", ROWS)
def test_gemini_inputs_same_as_reference2(ref2, row):
    card = tc.build_card(row, "A2", TAGS)
    assert card == ref2.build_card(row, "A2", TAGS)
    assert tc.doc_input(card) == ref2.gemini_doc_input(card)
    for q in [" 순한 아이 ", "흰색 소형견", "\t검정 고양이\n"]:
        assert tc.query_input(q) == ref2.gemini_query_input(q)


def test_phone_masked_before_api():
    card = tc.build_card(ROWS[1], "A1")
    sent = tc.doc_input(card)
    assert "010-1234-5678" not in sent and "[번호]" in sent and "12-345" not in sent
    assert sent.startswith("title: none | text: 진도견")
    assert tc.MODEL_KEY == "gemini-embedding-2:768"
