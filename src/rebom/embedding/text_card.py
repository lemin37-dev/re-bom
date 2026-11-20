"""공고 1건 → 글 카드 (A 템플릿). 규칙은 handoff_20261120/text_card_reference.py (LLM 인계 기준 구현) 와 글자까지 같다.

- Silver 칼럼 이름을 공공데이터 API 이름으로 바꿔 넣는다 (SILVER_TO_API).
- Silver 의 color_tag·size 는 쓰지 않는다. 원래 값(color_raw·weight_raw)에서 같은 규칙으로 다시 계산한다.
- 보호자 실명·연락처 칼럼은 읽지 않는다. 특징 글 안 전화번호는 [번호] 로 가리고 앞의 관리 번호는 지운다.
- A1 = 벤치마크 글 (smoke ① 카드 일치 확인용), A2 = A1 + 태그 (첫 적재).
- 11-18: 글도 gemini-embedding-2(768). 보낼 때는 doc_input()/query_input() 접두어를 붙인다
  전화번호 가리기는 build_card() 안에서 하므로 API 로 나가기 전에 끝난다.
- 11-20: 크기 경계는 템플릿마다 다르다. A1(벤치마크) 10kg 이하 소형, A2(Silver 규칙) 10kg 미만 소형 ·
  10~25kg 미만 중형 · 25kg 이상 대형. 사회성(sfeSoci)은 soci=True 일 때만 넣는다 (A2 포함 여부는 결정 필요).
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Iterable, Mapping

from rebom.embedding.gemini import DEFAULT_DIM, MODEL, document_text, query_text

TEMPLATE_VERSIONS = ("A1", "A2")
MODEL_KEY = f"{MODEL}:{DEFAULT_DIM}"  # 사진과 같은 값

# Silver(silver_animal_notice_*) → API 이름. sfeSoci 는 아직 Silver 에 없음 (A2 포함 여부 결정 필요).
SILVER_TO_API = {
    "breed_name_raw": "kindNm",
    "age_raw": "age",
    "sex_code": "sexCd",
    "neuter_code": "neuterYn",
    "weight_raw": "weight",
    "color_raw": "colorCd",
    "special_mark": "specialMark",
    "sfe_soci": "sfeSoci",
}

FAMILY = {"흰색계열": ["흰", "하얀", "하얗", "하양", "백", "화이트", "아이보리", "크림", "미색"],
          "검정계열": ["검", "흑", "블랙"],
          "갈색계열": ["갈", "브라운", "밤", "초코", "쵸코", "초콜", "쵸콜", "고동", "베이지"],
          "노랑계열": ["노란", "노랑", "금색", "골드", "살구", "겨자", "레몬", "황", "누렁", "치즈", "인절미"],
          "회색계열": ["회", "그레이", "실버"]}
BRINDLE = ["호반", "호피", "호구", "브린들", "호랑이"]
LEADING_ID = re.compile(r"^\s*(?:(?:개체\s*관리\s*번호|관리\s*번호)\s*\d+(?:-\d+)*|\d{2,}-\d+)\s*[-:：,，]?\s*")
PHONE = re.compile(r"0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}")
UNKNOWN_TAG = {"", "확인불가", "판단불가", "unknown", "none", "null", "nan"}


def _s(x: Any) -> str:
    """None·NaN → '' (벤치마크의 fillna('').astype(str) 와 같음)"""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return ""
    return str(x)


def color_tag(color: Any) -> list[str]:
    s2 = re.sub(r"흑갈|황갈|적갈|회갈|진갈", "갈", _s(color))
    fam = {k for k, ws in FAMILY.items() if any(w in s2 for w in ws)}
    if any(w in s2 for w in BRINDLE):
        fam.add("노랑계열")
    if not fam and any(w in s2 for w in ["어두운", "어둡"]):
        fam.add("검정계열")
    return [k for k in FAMILY if k in fam]


def kg(weight: Any) -> float | None:
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


SIZE_SMALL_MAX_INCLUSIVE = {"A1": True, "A2": False}  # A1: 10kg 이하 소형 (벤치마크) · A2: 10kg 미만 소형 (Silver)


def size_of(k: float | None, template: str = "A2") -> str:
    """몸무게(kg) → 소형/중형/대형. 기본은 Silver 규칙(A2). 정확히 10kg 은 A1 이면 소형, A2 면 중형."""
    if k is None or (isinstance(k, float) and math.isnan(k)):  # NaN 이 '대형'이 되는 것을 막음
        return ""
    small = k <= 10 if SIZE_SMALL_MAX_INCLUSIVE[template] else k < 10
    return "소형" if small else ("중형" if k < 25 else "대형")


def birth_year(age: Any) -> int | None:
    m = re.search(r"(\d{4})", _s(age))
    return int(m.group(1)) if m else None


def persona(row: Mapping[str, Any], soci: bool = False) -> str:
    """(soci 면 사회성 sfeSoci +) 특이사항 specialMark → 관리 번호 지우기 → 공백 정리 → 전화번호 가리기"""
    cols = ["sfeSoci", "specialMark"] if soci else ["specialMark"]
    s = " ".join(_s(row.get(c)) for c in cols if c in row)
    s = LEADING_ID.sub("", s, count=1)
    s = re.sub(r"\s+", " ", s).strip()
    return PHONE.sub("[번호]", s)


def build_card(row: Mapping[str, Any], template: str = "A2",
               tags: Iterable[tuple[str, Any]] | None = None, soci: bool = False) -> str:
    """row: API 이름 dict · tags: [(이름표, 값), …] (A2 에서만, 확인불가·빈 값은 뺌) · soci: 사회성 포함 (A1 은 불가)"""
    if template not in TEMPLATE_VERSIONS:
        raise ValueError(f"template 은 {TEMPLATE_VERSIONS} 중 하나")
    if template == "A1" and soci:
        raise ValueError("A1(벤치마크 글)에는 사회성을 넣지 않음")
    k = kg(row.get("weight"))
    y = birth_year(row.get("age"))
    ct = color_tag(row.get("colorCd"))
    kind = row.get("kindNm")
    color = row.get("colorCd")
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


def from_silver(row: Mapping[str, Any], include_soci: bool = False) -> dict[str, Any]:
    """Silver 한 줄 → API 이름 dict. 매핑에 없는 칼럼(보호자·연락처 포함)은 버린다.

    include_soci=False 면 sfeSoci 를 넣지 않는다 (A1 = 벤치마크와 같음). persona() 는 키가 있는지로
    판단하므로, 값이 없어도 키를 넣으면 앞에 공백이 생길 수 있어 아예 빼야 한다.
    """
    out = {api: row[col] for col, api in SILVER_TO_API.items() if col in row}
    if not include_soci:
        out.pop("sfeSoci", None)
    return out


def norm_for_hash(text: str) -> str:
    return " ".join(text.split())


def text_sha256(text: str) -> str:
    return hashlib.sha256(norm_for_hash(text).encode("utf-8")).hexdigest()


def card_from_silver(row: Mapping[str, Any], template: str = "A2",
                     tags: Iterable[tuple[str, Any]] | None = None) -> tuple[str, str]:
    """(카드, text_sha256)"""
    text = build_card(from_silver(row, include_soci=False), template, tags)
    return text, text_sha256(text)


def doc_input(card: str) -> str:
    """카드 → Gemini 에 보낼 문서 글 ("title: none | text: " + 카드)."""
    return document_text(card)


def query_input(query: str) -> str:
    """사용자 검색어 → Gemini 에 보낼 질의 글 ("task: search result | query: " + 검색어)."""
    return query_text(query)
