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
from rebom.serving.schemas import AnimalDetail, AnimalPage, Health, to_detail, to_summary


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
        region: str | None = Query(None, max_length=50),
        q: str | None = Query(None, max_length=100),
        page: int = Query(1, ge=1),
        size: int = Query(20, ge=1, le=100),
        r: AnimalRepository = Depends(repo),
    ) -> AnimalPage:
        query = AnimalQuery(
            species=species, state=state, sex=sex, region=region, q=q, page=page, size=size
        )
        total, rows = r.search(query)
        return AnimalPage(total=total, page=page, size=size, items=[to_summary(x) for x in rows])

    @app.get("/animals/{desertion_no}", response_model=AnimalDetail, response_model_by_alias=True)
    def get_animal(desertion_no: str, r: AnimalRepository = Depends(repo)) -> AnimalDetail:
        raw = r.get(desertion_no)
        if raw is None:
            raise HTTPException(status_code=404, detail="해당 구조번호의 동물이 없습니다.")
        return to_detail(raw)

    return app

