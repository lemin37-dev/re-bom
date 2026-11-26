"""A 템플릿 카드 — 기준 구현 (벤치마크 I1·H2의 '공고 글'과 글자까지 같게)

11-18: 글 모델도 Gemini Embedding 2 (사진과 같은 모델). 임베딩에 보낼 때는 gemini_doc_input() · gemini_query_input()의 접두어를 붙임
       (벤치마크 I2와 같은 형식). 카드는 Google API로 나가므로 전화번호 가리기(persona)는 반드시 보내기 전에

목적: 저장소의 text_card.py가 이 함수와 같은 결과를 내는지 맞춰 보는 기준
  - A1 = 벤치마크(I3·I5)에서 측정한 글. 서버 smoke ①(카드 일치)은 A1로 확인
  - A2 = A1 + 태그(비전 태깅 · Jev 태깅). 첫 적재는 A2 (11-17 결정)
입력: 공고 1건을 공공데이터 API 칼럼 이름으로 준 dict
  kindNm · age · sexCd · neuterYn · weight · colorCd · specialMark · sfeSoci(있으면)
  Silver 칼럼 이름이 다르면 이 이름으로 바꿔서 넣기 (예: special_mark → specialMark)
  Silver의 color_tag · size 칼럼은 쓰지 않음 → 벤치마크와 같게 원래 값(colorCd · weight)에서 다시 계산
  11-20: 크기 경계는 템플릿마다 다름 — A1(벤치마크)은 10kg 이하 소형, A2는 Silver 규칙(10kg 미만 소형 · 10~25kg 미만 중형 · 25kg 이상 대형)
실행: python text_card_reference.py   (자체 시험)
"""
import math
import re

TEMPLATE_VERSIONS = ("A1", "A2")
GEMINI_MODEL, GEMINI_DIM = "gemini-embedding-2", 768
DOC_PREFIX = "title: none | text: "               # 저장하는 카드
QUERY_PREFIX = "task: search result | query: "    # 검색할 때 사용자가 쓴 글

FAMILY = {"흰색계열": ["흰", "하얀", "하얗", "하양", "백", "화이트", "아이보리", "크림", "미색"],
          "검정계열": ["검", "흑", "블랙"],
          "갈색계열": ["갈", "브라운", "밤", "초코", "쵸코", "초콜", "쵸콜", "고동", "베이지"],
          "노랑계열": ["노란", "노랑", "금색", "골드", "살구", "겨자", "레몬", "황", "누렁", "치즈", "인절미"],
          "회색계열": ["회", "그레이", "실버"]}
BRINDLE = ["호반", "호피", "호구", "브린들", "호랑이"]
LEADING_ID = re.compile(r"^\s*(?:(?:개체\s*관리\s*번호|관리\s*번호)\s*\d+(?:-\d+)*|\d{2,}-\d+)\s*[-:：,，]?\s*")
PHONE = re.compile(r"0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}")
UNKNOWN_TAG = {"", "확인불가", "판단불가", "unknown", "none", "null", "nan"}


def _s(x):
    """None·NaN → '' (벤치마크의 fillna('').astype(str)와 같음)"""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return ""
    return str(x)


def color_tag(color):
    s2 = re.sub(r"흑갈|황갈|적갈|회갈|진갈", "갈", _s(color))
    fam = {k for k, ws in FAMILY.items() if any(w in s2 for w in ws)}
    if any(w in s2 for w in BRINDLE):
        fam.add("노랑계열")
    if not fam and any(w in s2 for w in ["어두운", "어둡"]):
        fam.add("검정계열")
    return [k for k in FAMILY if k in fam]


def kg(weight):
    t = re.sub(r"\.{2,}", ".", re.sub(r"(\d),(\d)", r"\1.\2", _s(weight)))
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t)]
    if not nums:
        return None
    if re.search(r"\d\s*g\s*\(", t):
        val = nums[0] / 1000
    elif len(nums) >= 2 and re.search(r"\d\s*[~\-]\s*\d", t):
        val = max(nums[0], nums[1])
    else:
        val = nums[0]
    return val if 0 < val <= 100 else None


SIZE_SMALL_MAX_INCLUSIVE = {"A1": True, "A2": False}   # A1: 10kg 이하 소형 (벤치마크) · A2: 10kg 미만 소형 (Silver 규칙, 11-20 사용자 결정)


def size_of(k, template="A2"):
    """몸무게(kg) → 소형/중형/대형. 기본값은 Silver 규칙(A2). 정확히 10kg은 A1이면 소형, A2면 중형"""
    if k is None or (isinstance(k, float) and math.isnan(k)):   # pandas NaN도 빈 값 (NaN이면 '대형'이 되는 것을 막음)
        return ""
    small = k <= 10 if SIZE_SMALL_MAX_INCLUSIVE[template] else k < 10
    return "소형" if small else ("중형" if k < 25 else "대형")


def birth_year(age):
    m = re.search(r"(\d{4})", _s(age))
    return int(m.group(1)) if m else None


def persona(row, soci=False):
    """(soci=True면 사회성 sfeSoci +) 특이사항 specialMark → 관리 번호 지우기 → 공백 정리 → 전화번호 가리기
    11-18: 사회성은 명시적으로 켤 때만 넣음. 벤치마크(I1)는 칼럼이 있으면 넣었지만 1,000마리 자료에는 sfeSoci 칼럼이 없었으므로
           기본값 False가 벤치마크 글과 같음. Silver 행을 통째로 넘겨도 A1에 사회성이 섞이지 않게 하려는 것"""
    s = " ".join(_s(row.get(c)) for c in (["sfeSoci", "specialMark"] if soci else ["specialMark"]) if c in row)
    s = LEADING_ID.sub("", s, count=1)
    s = re.sub(r"\s+", " ", s).strip()
    return PHONE.sub("[번호]", s)


def build_card(row, template="A2", tags=None, soci=False):
    """row: 공고 dict · tags: [(이름표, 값), …] (A2에서만 씀, 값이 확인불가·빈 값이면 뺌)
    soci: 사회성(sfeSoci)을 특징 앞에 넣을지. A2에 넣을지는 [결정 필요] → 정해지기 전에는 False. A1은 항상 False"""
    if template not in TEMPLATE_VERSIONS:
        raise ValueError(f"template은 {TEMPLATE_VERSIONS} 중 하나")
    if template == "A1" and soci:
        raise ValueError("A1(벤치마크 글)에는 사회성을 넣지 않음")
    k = kg(row.get("weight")); y = birth_year(row.get("age")); ct = color_tag(row.get("colorCd"))
    kind = row.get("kindNm"); color = row.get("colorCd")
    parts = [kind if isinstance(kind, str) else "",
             f"{y}년생" if y is not None else "",
             {"M": "수컷", "F": "암컷"}.get(_s(row.get("sexCd")), ""),
             {"Y": "중성화함", "N": "중성화 안 함"}.get(_s(row.get("neuterYn")), ""),
             f"{k:g}kg {size_of(k, template)}" if k is not None else "",
             ("털색 " + "·".join(ct)) if ct else "",
             f"(공고 털색 {color})" if isinstance(color, str) else ""]
    if template == "A2":
        for label, val in (tags or []):
            v = _s(val).strip()
            if v.lower() not in UNKNOWN_TAG:
                parts.append(f"{label} {v}")
    text = ", ".join(p for p in parts if p)
    p = persona(row, soci)
    return text + (". 특징: " + p if p else "")


def gemini_doc_input(card):
    """카드 → Gemini에 보낼 문서 글 (벤치마크 I2: "title: none | text: " + 카드)"""
    return DOC_PREFIX + card


def gemini_query_input(query):
    """사용자 검색어 → Gemini에 보낼 질의 글 (벤치마크 I2: "task: search result | query: " + 검색어)"""
    return QUERY_PREFIX + query.strip()


def norm_for_hash(text):
    """text_sha256을 만들 때 쓰는 공백 정리 (카드 저장·비교에 같은 규칙)"""
    return " ".join(text.split())


if __name__ == "__main__":
    ex = {"kindNm": "믹스견", "age": "2026(60일미만)(년생)", "sexCd": "M", "neuterYn": "N", "weight": "2.3(Kg)",
          "colorCd": "갈색&검정&흰색", "specialMark": "전염병감염위험, 케어필요"}
    want = "믹스견, 2026년생, 수컷, 중성화 안 함, 2.3kg 소형, 털색 흰색계열·검정계열·갈색계열, (공고 털색 갈색&검정&흰색). 특징: 전염병감염위험, 케어필요"
    got = build_card(ex, "A1")
    assert got == want, got
    a2 = build_card(ex, "A2", [("털 길이", "단모"), ("귀", "확인불가")])
    assert a2 == want.replace("(공고 털색 갈색&검정&흰색).", "(공고 털색 갈색&검정&흰색), 털 길이 단모."), a2
    assert persona({"specialMark": "관리번호 12-345 순함 문의 010-1234-5678"}) == "순함 문의 [번호]"
    assert build_card({**ex, "sfeSoci": "사람 좋아함"}, "A1") == want                      # 행을 통째로 넘겨도 A1에 사회성 없음
    assert build_card({**ex, "sfeSoci": "사람 좋아함"}, "A2", soci=True).endswith("특징: 사람 좋아함 전염병감염위험, 케어필요")
    assert kg("500g(60일미만)") == 0.5 and kg("3~4(Kg)") == 4.0 and kg("1,5(Kg)") == 1.5 and kg("") is None
    assert size_of(float("nan")) == "" and size_of(24.9) == "중형" and size_of(25) == "대형"
    assert size_of(10, "A1") == "소형" and size_of(10) == "중형" and size_of(9.99) == "소형" and size_of(10, "A2") == "중형"
    ten = {**ex, "weight": "10(Kg)"}
    assert "10kg 소형" in build_card(ten, "A1") and "10kg 중형" in build_card(ten, "A2")   # 정확히 10kg: A1은 벤치마크대로, A2는 Silver대로
    assert color_tag("흑갈색") == ["갈색계열"] and color_tag("호피") == ["노랑계열"]
    assert gemini_doc_input(got).startswith("title: none | text: 믹스견, 2026년생")
    assert gemini_query_input(" 순한 아이 ") == "task: search result | query: 순한 아이"
    print("자체 시험 통과")
    print("A1:", got)
    print("A2:", a2)
