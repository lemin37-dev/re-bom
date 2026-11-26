"""text_card.py 가 기준 구현(handoff_20261120/text_card_reference.py, LLM 인계본)과 글자까지 같은 카드를 내는지."""
import importlib.util
from pathlib import Path

import pytest

from rebom.embedding import text_card as tc

REF_PATH = (Path(__file__).resolve().parents[1] / "src" / "rebom" / "embedding" / "handoff_20261120"
            / "text_card_reference.py")


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
    {"kindNm": "믹스견", "weight": "10(Kg)", "colorCd": "흰색"},  # 경계: A1 소형, A2 중형
    {"kindNm": "믹스견", "weight": "24.7(Kg)", "colorCd": "흰색"},
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


@pytest.mark.parametrize("row", ROWS)
def test_gemini_inputs_same_as_reference(ref, row):
    card = tc.build_card(row, "A2", TAGS)
    assert tc.doc_input(card) == ref.gemini_doc_input(card)
    for q in [" 순한 아이 ", "흰색 소형견", "\t검정 고양이\n"]:
        assert tc.query_input(q) == ref.gemini_query_input(q)


def test_soci_only_when_asked(ref):
    row = ROWS[3]
    assert "사람 좋아함" not in tc.build_card(row, "A2")
    assert tc.build_card(row, "A2", soci=True) == ref.build_card(row, "A2", soci=True)
    assert "사람 좋아함" in tc.build_card(row, "A2", soci=True)
    with pytest.raises(ValueError):
        tc.build_card(row, "A1", soci=True)


def test_size_rule_by_template():
    assert tc.size_of(9.9) == "소형" and tc.size_of(10) == "중형" and tc.size_of(24.7) == "중형"
    assert tc.size_of(25) == "대형" and tc.size_of(10, "A1") == "소형" and tc.size_of(float("nan")) == ""


def test_phone_masked_before_api():
    card = tc.build_card(ROWS[1], "A1")
    sent = tc.doc_input(card)
    assert "010-1234-5678" not in sent and "[번호]" in sent and "12-345" not in sent
    assert sent.startswith("title: none | text: 진도견")
    assert tc.MODEL_KEY == "gemini-embedding-2:768"


def test_hash_is_of_masked_template_card():
    """규칙 4 (Gold 공통 규칙 초안): 입력 해시 = 실제로 넣는 값. 카드는 build_card 안에서 이미 번호를 가리고
    관리 번호를 지운 템플릿 본문이고, 해시는 이 본문(접두어 없이, 공백 정리)으로 만든다."""
    silver = {"breed_name_raw": "진도견", "weight_raw": "3(Kg)", "color_raw": "흰색",
              "special_mark": "관리번호 12-345 순함 문의 010-1234-5678", "care_tel": "010-9999-9999"}
    text, sha = tc.card_from_silver(silver, "A2")
    assert "010-" not in text and "12-345" not in text and "[번호]" in text
    assert sha == tc.text_sha256(text) and tc.doc_input(text).endswith(text)
