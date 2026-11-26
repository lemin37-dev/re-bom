"""dev 모드(REBOM_ENV=dev)에서만 붙이는 검색 순위 근거. 로컬 개발 때 왜 이 순서로 나왔는지 바로 보려고 쓴다.

prod(기본값)에서는 부르지 않고, 응답에서도 rank·debug 키가 빠진다(schemas._DevOnly).
순서는 저장소(repository.py·db_repository.py)가 정하고, 여기서는 이미 정해진 순서에 쓰인 값만 다시 읽어 설명한다.
  1. 필터(WHERE) 통과. 태그를 골랐으면 털처럼 반대면 빼는(exclusive) 범주가 반대인 아이는 제외
  2. 태그 단계 all → some → unknown → other (태그를 골랐을 때만)
  3. 글·사진 벡터 코사인 거리 가까운 순 (질의 벡터가 있을 때만, 임계값 없음. 벡터 없는 공고·종료 공고는 뒤)
  4. 기본 정렬 (구조일 최신순 또는 공고 종료일 가까운 순)
"""
from __future__ import annotations

from datetime import date
from typing import Any

from rebom.serving.derive import age_years, sido, weight_kg
from rebom.serving.repository import AnimalQuery, SearchPage, _words, shelter_list
from rebom.serving.tags import tag_detail, tag_match

TEXT_FIELDS = ("specialMark", "kindFullNm", "colorCd", "happenPlace", "sfeSoci")  # 검색어(q)를 찾는 칸
KIND_LABEL = {"text": "글", "image": "사진"}
SORT_LABEL = {"latest": "구조일 최신순", "notice_end": "공고 종료일 가까운 순(끝난 공고는 뒤)"}
SORT_FIELD = {"latest": ("happenDate", "happenDt"), "notice_end": ("noticeEnd", "noticeEdt")}


def _age(raw: dict[str, Any], today: date) -> int | None:
    return raw["_ageYears"] if "_ageYears" in raw else age_years(raw.get("age"), today)


def _kg(raw: dict[str, Any]) -> float | None:
    return raw["_weightKg"] if "_weightKg" in raw else weight_kg(raw.get("weight"), raw.get("upKindNm"))


def filter_hits(raw: dict[str, Any], query: AnimalQuery, today: date) -> list[dict[str, Any]]:
    """걸린 필터마다 조건과 이 공고의 실제 값. 조건을 안 준 필터는 넣지 않는다."""
    hits: list[dict[str, Any]] = []

    def add(name: str, condition: str, value: Any) -> None:
        hits.append({"name": name, "condition": condition, "value": None if value is None else str(value)})

    if query.species:
        add("species", query.species, f"{raw.get('upKindNm')} ({raw.get('upKindCd')})")
    if query.state:
        add("state", query.state, raw.get("processState"))
    if query.sex:
        add("sex", query.sex, raw.get("sexCd"))
    if query.shelter:
        add("shelter", ",".join(shelter_list(query.shelter)), f"{raw.get('careRegNo')} {raw.get('careNm') or ''}".strip())
    if query.regions:
        add("region", ",".join(query.regions),
            f"보호소 {sido(raw.get('careAddr')) or '-'} / 기관 {sido(raw.get('orgNm')) or '-'}")
    if query.age_min is not None or query.age_max is not None:
        age = _age(raw, today)
        add("age", f"{query.age_min if query.age_min is not None else ''}~{query.age_max if query.age_max is not None else ''}살",
            None if age is None else f"{age}살")
    if query.weight_min is not None or query.weight_lt is not None:
        lo = f"{query.weight_min}kg 이상" if query.weight_min is not None else ""
        hi = f"{query.weight_lt}kg 미만" if query.weight_lt is not None else ""
        kg = _kg(raw)
        add("weight", " ".join(x for x in (lo, hi) if x), None if kg is None else f"{kg}kg")
    words = _words(query.q)
    if words:
        found = [f"{w}→{k}" for w in words for k in TEXT_FIELDS if w in (raw.get(k) or "")]
        add("q", " ".join(words), ", ".join(found) or None)
    return hits


def explain(raw: dict[str, Any], tags: frozenset[str], query: AnimalQuery, position: int, today: date) -> dict[str, Any]:
    """공고 한 마리의 순위 근거 (schemas.RankDebug 모양)."""
    sort_name, sort_key = SORT_FIELD[query.sort]
    out: dict[str, Any] = {
        "position": position,
        "filters": filter_hits(raw, query, today),
        "tag_match": tag_match(tags, query.tags) if query.tags else None,
        "tags": tag_detail(tags, query.tags) if query.tags else [],
        "vector": None,
        "sort_key": sort_name,
        "sort_value": raw.get(sort_key),
    }
    if query.vector:
        spec = query.vector.spec
        out["vector"] = {"kind": spec.kind, "version": spec.version, "model": spec.model,
                         "distance": raw.get("_distance"), "similarity": raw.get("_similarity")}
    return out


def page_debug(query: AnimalQuery, result: SearchPage, *, env: str, data_mode: str,
               text_as_words: str | None = None) -> dict[str, Any]:
    """페이지 단위 설명: 정렬 순서 한 줄씩, 제외한 수, 질의 벡터 (schemas.PageDebug 모양)."""
    applied = _applied_filters(query)
    order = [f"필터(WHERE): {', '.join(applied) if applied else '없음'}"]
    if text_as_words:
        order.append(f"sample 모드라 문장 '{text_as_words}' 을 단어 포함(q)으로 거름 (벡터 없음)")
    if query.tags:
        order.append(f"반대 태그 제외(털): {result.excluded}마리")
    step = 1
    if query.tags:
        order.append(f"{step}순위 태그 단계 all → some → unknown → other (모두 맞음 {result.matched or 0}마리)")
        step += 1
    if query.vector:
        order.append(f"{step}순위 {KIND_LABEL[query.vector.spec.kind]} 벡터 코사인 거리 가까운 순 "
                     "(임계값 없음, 벡터 없는 공고·종료 공고는 뒤)")
        step += 1
    order.append(f"{step}순위 {SORT_LABEL[query.sort]}")
    vector = None
    if query.vector:
        spec = query.vector.spec
        vector = {"kind": spec.kind, "version": spec.version, "model": spec.model, "dim": spec.dim}
    return {"env": env, "data_mode": data_mode, "order": order, "excluded": result.excluded, "vector": vector}


def _applied_filters(query: AnimalQuery) -> list[str]:
    out = []
    for name in ("species", "state", "sex"):
        if getattr(query, name):
            out.append(f"{name}={getattr(query, name)}")
    if query.shelter:
        out.append(f"shelter={query.shelter}")
    if query.regions:
        out.append(f"region={','.join(query.regions)}")
    if query.age_min is not None or query.age_max is not None:
        out.append(f"age={query.age_min if query.age_min is not None else ''}~{query.age_max if query.age_max is not None else ''}")
    if query.weight_min is not None:
        out.append(f"weight>={query.weight_min}")
    if query.weight_lt is not None:
        out.append(f"weight<{query.weight_lt}")
    if _words(query.q):
        out.append(f"q={' '.join(_words(query.q))}")
    return out
