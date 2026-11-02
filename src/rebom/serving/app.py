"""Re:봄 검색 API (FastAPI).

실행 (레포 루트에서, pip install -e ".[serve]" 후):
    uvicorn rebom.serving.app:create_app --factory --reload
"""
from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from rebom.common.config import Settings, get_settings
from rebom.serving.repository import AnimalQuery, AnimalRepository, build_repository
from rebom.serving.schemas import (
    AnimalDetail,
    AnimalPage,
    Health,
    ShelterList,
    ShelterSummary,
    shelter_of,
    to_detail,
    to_summary,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Re:봄 검색 API", version="0.1.0")
    app.state.settings = settings
    app.state.repo = build_repository(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    def repo(request: Request) -> AnimalRepository:
        return request.app.state.repo

    @app.get("/health", response_model=Health, response_model_by_alias=True)
    def health(r: AnimalRepository = Depends(repo)) -> Health:
        return Health(status="ok", data_mode=settings.data_mode.value, animal_count=r.count())

    @app.get("/animals", response_model=AnimalPage, response_model_by_alias=True)
    def list_animals(
        species: Literal["dog", "cat", "etc"] | None = None,
        state: Literal["protect", "ended"] | None = None,
        sex: Literal["M", "F", "Q"] | None = None,
        region: str | None = Query(
            None, max_length=100, description="시·도 단어, 쉼표로 여러 개 (예: 서울,경기)"
        ),
        shelter: str | None = Query(None, max_length=30, description="보호소 등록번호(careRegNo)"),
        q: str | None = Query(None, max_length=200, description="띄어쓰기로 나눈 단어 중 하나라도 포함"),
        age_min: int | None = Query(None, ge=0, le=40),
        age_max: int | None = Query(None, ge=0, le=40),
        weight_min: float | None = Query(None, ge=0),
        weight_lt: float | None = Query(None, gt=0),
        sort: Literal["latest", "notice_end"] = "latest",
        page: int = Query(1, ge=1),
        size: int = Query(20, ge=1, le=100),
        r: AnimalRepository = Depends(repo),
    ) -> AnimalPage:
        regions = tuple(t.strip() for t in (region or "").split(",") if t.strip())
        query = AnimalQuery(
            species=species, state=state, sex=sex, regions=regions, shelter=shelter, q=q,
            age_min=age_min, age_max=age_max, weight_min=weight_min, weight_lt=weight_lt,
            sort=sort, page=page, size=size,
        )
        total, rows = r.search(query)
        return AnimalPage(total=total, page=page, size=size, items=[to_summary(x) for x in rows])

    @app.get("/shelters", response_model=ShelterList, response_model_by_alias=True)
    def list_shelters(r: AnimalRepository = Depends(repo)) -> ShelterList:
        items = [
            ShelterSummary(**shelter_of(row).model_dump(), animal_count=n) for row, n in r.shelters()
        ]
        return ShelterList(total=len(items), items=items)

    @app.get("/animals/{desertion_no}", response_model=AnimalDetail, response_model_by_alias=True)
    def get_animal(desertion_no: str, r: AnimalRepository = Depends(repo)) -> AnimalDetail:
        raw = r.get(desertion_no)
        if raw is None:
            raise HTTPException(status_code=404, detail="해당 구조번호의 동물이 없습니다.")
        return to_detail(raw)

    return app

