"""동물 데이터 소스. REBOM_DATA_MODE 에 따라 sample(JSON 파일) 또는 db 를 쓴다."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol

from rebom.common.config import DataMode, Settings
from rebom.serving.derive import age_years, sido, weight_kg

# 축종 코드 (docs/rescue_fields.md)
SPECIES_CODES = {"dog": "417000", "cat": "422400", "etc": "429900"}

SortKey = Literal["latest", "notice_end"]


@dataclass(frozen=True)
class AnimalQuery:
    species: str | None = None  # dog / cat / etc
    state: str | None = None  # protect(보호중) / ended(종료)
    sex: str | None = None  # M / F / Q
    # 시·도 이름에 들어가는 단어 중 하나라도 맞으면 통과 (예: ("서울", "경기"))
    regions: tuple[str, ...] = ()
    shelter: str | None = None  # 보호소 등록번호(careRegNo)
    q: str | None = None  # 띄어쓰기로 나눈 단어 중 하나라도 특징·품종·색상·발견 장소에 있으면 통과
    age_min: int | None = None  # 추정 나이(살), 이상
    age_max: int | None = None  # 이하
    weight_min: float | None = None  # kg, 이상
    weight_lt: float | None = None  # kg, 미만
    sort: SortKey = "latest"
    page: int = 1
    size: int = 20


class AnimalRepository(Protocol):
    def search(self, query: AnimalQuery) -> tuple[int, list[dict[str, Any]]]: ...

    def get(self, desertion_no: str) -> dict[str, Any] | None: ...

    def shelters(self) -> list[tuple[dict[str, Any], int]]: ...

    def count(self) -> int: ...


def _words(q: str | None) -> list[str]:
    # 한 글자 단어(조사 등)는 너무 많이 걸려서 뺀다
    return [w for w in (q or "").split() if len(w) >= 2]


def _matches(raw: dict[str, Any], query: AnimalQuery, today: date) -> bool:
    if query.species and raw.get("upKindCd") != SPECIES_CODES[query.species]:
        return False
    state = raw.get("processState") or ""
    if query.state == "protect" and state != "보호중":
        return False
    if query.state == "ended" and not state.startswith("종료"):
        return False
    if query.sex and raw.get("sexCd") != query.sex:
        return False
    if query.shelter and raw.get("careRegNo") != query.shelter:
        return False
    if query.regions:
        names = f"{sido(raw.get('careAddr')) or ''} {sido(raw.get('orgNm')) or ''}"
        if not any(token in names for token in query.regions):
            return False
    if query.age_min is not None or query.age_max is not None:
        age = age_years(raw.get("age"), today)
        if age is None:
            return False
        if query.age_min is not None and age < query.age_min:
            return False
        if query.age_max is not None and age > query.age_max:
            return False
    if query.weight_min is not None or query.weight_lt is not None:
        kg = weight_kg(raw.get("weight"))
        if kg is None:
            return False
        if query.weight_min is not None and kg < query.weight_min:
            return False
        if query.weight_lt is not None and kg >= query.weight_lt:
            return False
    words = _words(query.q)
    if words:
        text = " ".join(
            raw.get(k, "") for k in ("specialMark", "kindFullNm", "colorCd", "happenPlace", "sfeSoci")
        )
        if not any(w in text for w in words):
            return False
    return True


def _sort_key(sort: SortKey, today: date):
    today_s = today.strftime("%Y%m%d")
    if sort == "notice_end":
        # 아직 공고 중인 아이를 종료일 가까운 순으로 먼저, 공고가 끝난 아이는 그 뒤
        def key(r: dict[str, Any]):
            end = r.get("noticeEdt") or "99999999"
            return (end < today_s, end, r["desertionNo"])

        return key, False
    return (lambda r: (r.get("happenDt", ""), r["desertionNo"])), True


class SampleRepository:
    """data/sample/ 의 구조동물 샘플을 메모리에 올려 두고 필터링한다."""

    def __init__(self, path: Path):
        with path.open(encoding="utf-8") as f:
            rows = json.load(f)
        # desertionNo 없는 빈 항목은 버린다. 혹시 남아 있을 대표자 실명은 메모리에도 두지 않는다
        self._rows = [
            {k: v for k, v in r.items() if k != "careOwnerNm"}
            for r in rows
            if r.get("desertionNo")
        ]
        self._by_id = {r["desertionNo"]: r for r in self._rows}

    def search(self, query: AnimalQuery, today: date | None = None) -> tuple[int, list[dict[str, Any]]]:
        today = today or date.today()
        hits = [r for r in self._rows if _matches(r, query, today)]
        key, reverse = _sort_key(query.sort, today)
        hits.sort(key=key, reverse=reverse)
        start = (query.page - 1) * query.size
        return len(hits), hits[start : start + query.size]

    def get(self, desertion_no: str) -> dict[str, Any] | None:
        return self._by_id.get(desertion_no)

    def shelters(self) -> list[tuple[dict[str, Any], int]]:
        """보호소별 대표 행과 보호중 동물 수. 보호중 많은 순."""
        first: dict[str, dict[str, Any]] = {}
        protected: dict[str, int] = defaultdict(int)
        for r in self._rows:
            reg = r.get("careRegNo")
            if not reg:
                continue
            first.setdefault(reg, r)
            if r.get("processState") == "보호중":
                protected[reg] += 1
        out = [(row, protected[reg]) for reg, row in first.items()]
        out.sort(key=lambda x: (-x[1], x[0].get("careNm") or ""))
        return out

    def count(self) -> int:
        return len(self._rows)


def build_repository(settings: Settings) -> AnimalRepository:
    if settings.data_mode == DataMode.SAMPLE:
        return SampleRepository(settings.sample_path)
    raise NotImplementedError(
        "REBOM_DATA_MODE=db 는 아직 구현되지 않았습니다. 지금은 REBOM_DATA_MODE=sample 을 쓰세요."
    )
