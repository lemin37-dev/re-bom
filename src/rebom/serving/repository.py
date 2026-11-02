"""동물 데이터 소스. REBOM_DATA_MODE 에 따라 sample(JSON 파일) 또는 db 를 쓴다."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from rebom.common.config import DataMode, Settings

# 축종 코드 (docs/rescue_fields.md)
SPECIES_CODES = {"dog": "417000", "cat": "422400", "etc": "429900"}


@dataclass(frozen=True)
class AnimalQuery:
    species: str | None = None  # dog / cat / etc
    state: str | None = None  # protect(보호중) / ended(종료)
    sex: str | None = None  # M / F / Q
    region: str | None = None  # 보호소 주소·관할 기관에 포함된 문자열 (예: "서울", "수원시")
    q: str | None = None  # 특징 문장·품종·색상·발견 장소에서 찾는 단어
    page: int = 1
    size: int = 20


class AnimalRepository(Protocol):
    def search(self, query: AnimalQuery) -> tuple[int, list[dict[str, Any]]]: ...

    def get(self, desertion_no: str) -> dict[str, Any] | None: ...

    def count(self) -> int: ...


def _matches(raw: dict[str, Any], query: AnimalQuery) -> bool:
    if query.species and raw.get("upKindCd") != SPECIES_CODES[query.species]:
        return False
    state = raw.get("processState") or ""
    if query.state == "protect" and state != "보호중":
        return False
    if query.state == "ended" and not state.startswith("종료"):
        return False
    if query.sex and raw.get("sexCd") != query.sex:
        return False
    if query.region:
        where = f"{raw.get('careAddr', '')} {raw.get('orgNm', '')}"
        if query.region not in where:
            return False
    if query.q:
        text = " ".join(
            raw.get(k, "") for k in ("specialMark", "kindFullNm", "colorCd", "happenPlace")
        )
        if query.q not in text:
            return False
    return True


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
        # 최근 접수가 먼저
        self._rows.sort(key=lambda r: (r.get("happenDt", ""), r["desertionNo"]), reverse=True)
        self._by_id = {r["desertionNo"]: r for r in self._rows}

    def search(self, query: AnimalQuery) -> tuple[int, list[dict[str, Any]]]:
        hits = [r for r in self._rows if _matches(r, query)]
        start = (query.page - 1) * query.size
        return len(hits), hits[start : start + query.size]

    def get(self, desertion_no: str) -> dict[str, Any] | None:
        return self._by_id.get(desertion_no)

    def count(self) -> int:
        return len(self._rows)


def build_repository(settings: Settings) -> AnimalRepository:
    if settings.data_mode == DataMode.SAMPLE:
        return SampleRepository(settings.sample_path)
    raise NotImplementedError(
        "REBOM_DATA_MODE=db 는 아직 구현되지 않았습니다. 지금은 REBOM_DATA_MODE=sample 을 쓰세요."
    )
