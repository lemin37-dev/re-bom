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
from rebom.serving.tags import MATCH_RANK, TagGroups, count_tags, load_sample_tags, tag_match

# 축종 코드 (docs/rescue_fields.md)
SPECIES_CODES = {"dog": "417000", "cat": "422400", "etc": "429900"}

SortKey = Literal["latest", "notice_end"]
VectorKind = Literal["text", "image"]


class VectorUnavailable(RuntimeError):
    """벡터 검색에 필요한 설정·키가 없다. 사람이 채워야 하며, 메시지에 무엇을 할지 적는다."""


@dataclass(frozen=True)
class VectorSpec:
    """질의를 임베딩할 조건. RDS active_version 의 version 과 query_config(그 버전의 Gold 레지스트리 model_id·dim)에서 온다.
    저장 벡터와 같은 공간이 되도록 질의도 이 모델·차원으로만 임베딩하고, 벡터 표에서는 이 버전 행만 비교한다."""

    kind: VectorKind
    version: str  # 예: text_embedding@3
    model: str  # 예: gemini-embedding-2
    dim: int  # 예: 768


@dataclass(frozen=True)
class QueryVector:
    spec: VectorSpec
    values: tuple[float, ...]


@dataclass(frozen=True)
class AnimalQuery:
    species: str | None = None  # dog / cat / etc
    state: str | None = None  # protect(보호중) / ended(종료)
    sex: str | None = None  # M / F / Q
    # 시·도 이름에 들어가는 단어 중 하나라도 맞으면 통과 (예: ("서울", "경기"))
    regions: tuple[str, ...] = ()
    shelter: str | None = None  # 보호소 등록번호(careRegNo), 쉼표로 여러 개 (같은 건물에 시·군마다 따로 등록된 곳)
    q: str | None = None  # 띄어쓰기로 나눈 단어 중 하나라도 특징·품종·색상·발견 장소에 있으면 통과
    age_min: int | None = None  # 추정 나이(살), 이상
    age_max: int | None = None  # 이하
    weight_min: float | None = None  # kg, 이상
    weight_lt: float | None = None  # kg, 미만
    tags: TagGroups = ()  # 사진 태그. 거르지 않고 맞는 아이를 앞에 둔다 (serving/tags.py)
    # 글·사진 질의 벡터. 거르지 않고 가까운 순으로 앞에 둔다. 벡터가 없는 공고는 뒤 (db 모드만)
    vector: QueryVector | None = None
    sort: SortKey = "latest"
    page: int = 1
    size: int = 20


@dataclass(frozen=True)
class SearchPage:
    total: int
    rows: list[dict[str, Any]]
    matched: int | None = None  # 태그를 골랐을 때 모두 맞는(all) 아이 수
    excluded: int = 0  # 태그를 골랐을 때 털처럼 반대면 빼는 범주가 반대라서 뺀 아이 수


@dataclass(frozen=True)
class VectorStats:
    """질의 벡터와의 similarity 분포. 필터(WHERE)를 통과한 공고 전체 기준(털 반대 제외 전). dev 모드 순위 근거용."""

    scored: int  # 거리가 있는 공고 수
    missing: int  # 거리가 없는 공고 수 (벡터 없음 또는 종료 공고)
    top: float | None  # 가장 높은 similarity
    nth: float | None  # similarity 높은 순 NTH 번째 값 (공고가 그보다 적으면 None)
    median: float | None


STATS_NTH = 100


class AnimalRepository(Protocol):
    def search(self, query: AnimalQuery) -> SearchPage: ...

    def get(self, desertion_no: str) -> dict[str, Any] | None: ...

    def shelters(self) -> list[tuple[dict[str, Any], int]]: ...

    def places(self, query: AnimalQuery) -> list[tuple[str, int]]:
        """목록 검색과 같은 조건에 맞는 아이들의 보호소별 마릿수 (지도용). 많은 순."""
        ...

    def count(self) -> int: ...

    def tag_counts(self) -> dict[str, int]: ...

    def tags_of(self, desertion_no: str) -> frozenset[str]: ...

    def vector_spec(self, kind: VectorKind) -> VectorSpec:
        """활성 버전의 질의 조건. 없으면 VectorUnavailable."""
        ...

    def vector_stats(self, query: AnimalQuery) -> VectorStats | None:
        """query.vector 와의 similarity 분포 (dev 모드에서만 부른다). 벡터가 없으면 None."""
        ...


def shelter_list(shelter: str | None) -> tuple[str, ...]:
    """'311,312' → ('311', '312')."""
    return tuple(s.strip() for s in (shelter or "").split(",") if s.strip())


def count_places(rows: list[tuple[str | None, frozenset[str] | None]], query: AnimalQuery) -> list[tuple[str, int]]:
    """(보호소 번호, 태그) 행 → 보호소별 마릿수. 태그를 골랐으면 목록처럼 털이 반대인 아이(tag_match None)는 뺀다. 많은 순."""
    counts: dict[str, int] = defaultdict(int)
    for reg, tags in rows:
        if not reg:
            continue
        if query.tags and tag_match(tags or frozenset(), query.tags) is None:
            continue
        counts[reg] += 1
    return sorted(counts.items(), key=lambda x: (-x[1], x[0]))


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
    if query.shelter and raw.get("careRegNo") not in shelter_list(query.shelter):
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
        kg = weight_kg(raw.get("weight"), raw.get("upKindNm"))
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
    """data/sample/ 의 구조동물 샘플을 메모리에 올려 두고 필터링한다.

    사진 태그는 같은 폴더의 photo_tags.json(scripts/serve/make_sample_photo_tags.py 가 만든 실제 태거 결과)에서 읽는다.
    """

    def __init__(self, path: Path, tags_path: Path | None = None):
        with path.open(encoding="utf-8") as f:
            rows = json.load(f)
        # desertionNo 없는 빈 항목은 버린다. 혹시 남아 있을 대표자 실명은 메모리에도 두지 않는다
        self._rows = [
            {k: v for k, v in r.items() if k != "careOwnerNm"}
            for r in rows
            if r.get("desertionNo")
        ]
        self._by_id = {r["desertionNo"]: r for r in self._rows}
        self._tags = load_sample_tags(tags_path or path.with_name("photo_tags.json"))

    def search(self, query: AnimalQuery, today: date | None = None) -> SearchPage:
        today = today or date.today()
        hits = [r for r in self._rows if _matches(r, query, today)]
        key, reverse = _sort_key(query.sort, today)
        hits.sort(key=key, reverse=reverse)
        matched, excluded = None, 0
        if query.tags:
            ranked = [(r, tag_match(self.tags_of(r["desertionNo"]), query.tags)) for r in hits]
            excluded = sum(1 for _, m in ranked if m is None)
            ranked = [(r, m) for r, m in ranked if m is not None]
            ranked.sort(key=lambda x: MATCH_RANK[x[1]])  # 안정 정렬이라 같은 단계 안에서는 고른 정렬이 유지된다
            hits = [r for r, _ in ranked]
            matched = sum(1 for _, m in ranked if m == "all")
        start = (query.page - 1) * query.size
        return SearchPage(len(hits), hits[start : start + query.size], matched, excluded)

    def get(self, desertion_no: str) -> dict[str, Any] | None:
        return self._by_id.get(desertion_no)

    def places(self, query: AnimalQuery, today: date | None = None) -> list[tuple[str, int]]:
        today = today or date.today()
        hits = [r for r in self._rows if _matches(r, query, today)]
        return count_places([(r.get("careRegNo"), self.tags_of(r["desertionNo"])) for r in hits], query)

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

    def tags_of(self, desertion_no: str) -> frozenset[str]:
        return self._tags.get(desertion_no, frozenset())

    def tag_counts(self) -> dict[str, int]:
        """태그별 보호중 동물 수 (검색 화면은 보호중만 보여 준다)."""
        protected = {r["desertionNo"] for r in self._rows if r.get("processState") == "보호중"}
        return dict(count_tags(tags for no, tags in self._tags.items() if no in protected))

    def vector_spec(self, kind: VectorKind) -> VectorSpec:
        raise VectorUnavailable("sample 모드에는 벡터가 없습니다")

    def vector_stats(self, query: AnimalQuery) -> VectorStats | None:
        return None


def build_repository(settings: Settings) -> AnimalRepository:
    if settings.data_mode == DataMode.SAMPLE:
        return SampleRepository(settings.sample_path)
    from rebom.serving.db_repository import DbRepository, connect_from_settings

    return DbRepository(connect_from_settings(settings.db_dsn), settings.image_base_url)
