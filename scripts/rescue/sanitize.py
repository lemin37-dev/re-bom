"""샘플에 올리기 전 개인정보 처리. 로컬 원본은 건드리지 않고 사본에만 적용한다.

- careOwnerNm(보호소 대표자 이름): 삭제
- careAddr: 시·도, 시·군·구까지만 남김
- 휴대전화 모양(01X-XXXX-XXXX)은 어느 문자열 필드에서든 뒤 4자리를 ****로 가림
  (보호소 대표 번호 같은 일반 전화는 기관 연락처라 그대로 둔다)
- 식별번호·URL·날짜 필드(NO_MASK)는 숫자열이라 건드리지 않는다
"""
from __future__ import annotations

import re

DROP_FIELDS = ("careOwnerNm",)
NO_MASK = {"desertionNo", "careRegNo", "noticeNo", "rfidCd", "updTm", "happenDt", "noticeSdt", "noticeEdt"}
MOBILE_RE = re.compile(r"(01[016789])[-\s.]?(\d{3,4})[-\s.]?(\d{4})")


def _skip(key: str) -> bool:
    return key in NO_MASK or key.startswith("popfile") or key.endswith("Img")


def sanitize(rec: dict) -> dict:
    out = {k: v for k, v in rec.items() if k not in DROP_FIELDS}
    if out.get("careAddr"):
        out["careAddr"] = " ".join(out["careAddr"].split()[:2])
    for k, v in out.items():
        if isinstance(v, str) and not _skip(k):
            out[k] = MOBILE_RE.sub(lambda m: f"{m.group(1)}-{m.group(2)}-****", v)
    return out
