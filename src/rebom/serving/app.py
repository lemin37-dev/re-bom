"""Re:봄 검색 API (FastAPI).

실행 (레포 루트에서, pip install -e ".[serve]" 후):
    uvicorn rebom.serving.app:create_app --factory --reload

글·사진으로 찾기(GET /animals?text=…, POST /animals/photo-search)는 db 모드에서만 된다. 질의를 Gemini 로 임베딩하므로
요청마다 비용이 든다(사진 1장 약 $0.000116, embedding/gemini.py 실측).
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from rebom.common.config import Settings, get_settings
from rebom.serving.query_embed import BadQueryImage, Embedder, QueryEmbedder, default_embedder
from rebom.serving.repository import AnimalQuery, AnimalRepository, QueryVector, VectorKind, build_repository
from rebom.serving.schemas import (
    AnimalDetail,
    AnimalPage,
    Health,
    ShelterList,
    ShelterSummary,
    TagCatalog,
    shelter_of,
    to_detail,
    to_summary,
)
from rebom.serving.tags import catalog, parse_tags, tag_match

log = logging.getLogger(__name__)
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
KIND_LABEL = {"text": "글", "image": "사진"}


def filters(
    species: Literal["dog", "cat", "etc"] | None = None,
    state: Literal["protect", "ended"] | None = None,
    sex: Literal["M", "F", "Q"] | None = None,
    region: str | None = Query(None, max_length=100, description="시·도 단어, 쉼표로 여러 개 (예: 서울,경기)"),
    shelter: str | None = Query(None, max_length=30, description="보호소 등록번호(careRegNo)"),
    q: str | None = Query(None, max_length=200, description="띄어쓰기로 나눈 단어 중 하나라도 포함"),
    age_min: int | None = Query(None, ge=0, le=40),
    age_max: int | None = Query(None, ge=0, le=40),
    weight_min: float | None = Query(None, ge=0),
    weight_lt: float | None = Query(None, gt=0),
    tags: str | None = Query(
        None, max_length=300,
        description="사진 태그 코드, 쉼표로 여러 개 (예: ear.drop,fur.fluffy). 거르지 않고 맞는 아이를 앞에 둔다. 털이 반대인 아이만 뺀다",
    ),
    sort: Literal["latest", "notice_end"] = "latest",
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
) -> AnimalQuery:
    regions = tuple(t.strip() for t in (region or "").split(",") if t.strip())
    try:
        tag_groups = parse_tags(tags)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return AnimalQuery(
        species=species, state=state, sex=sex, regions=regions, shelter=shelter, q=q,
        age_min=age_min, age_max=age_max, weight_min=weight_min, weight_lt=weight_lt,
        tags=tag_groups, sort=sort, page=page, size=size,
    )


def create_app(settings: Settings | None = None,
               embedder_factory: Callable[[int], Embedder] = default_embedder) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Re:봄 검색 API", version="0.1.0")
    app.state.settings = settings
    app.state.repo = build_repository(settings)
    app.state.embedder = QueryEmbedder(embedder_factory)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    def repo(request: Request) -> AnimalRepository:
        return request.app.state.repo

    def embed(r: AnimalRepository, kind: VectorKind, make: Callable, data: str | bytes) -> QueryVector:
        spec = r.vector_spec(kind)
        if spec is None:
            raise HTTPException(status_code=503, detail=f"{KIND_LABEL[kind]} 벡터 검색을 쓸 수 없습니다 "
                                                        "(db 모드가 아니거나 활성 버전이 없음).")
        try:
            return make(spec, data)
        except BadQueryImage as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except Exception:  # noqa: BLE001 - 키·네트워크·모델 오류. 내용은 로그에만 남긴다
            log.exception("질의 임베딩 실패 (%s)", kind)
            raise HTTPException(status_code=503, detail="질의를 임베딩하지 못했습니다. 잠시 뒤 다시 시도해 주세요.") from None

    def page_of(r: AnimalRepository, query: AnimalQuery) -> AnimalPage:
        result = r.search(query)
        items = []
        for x in result.rows:
            tags = x["_tags"] if "_tags" in x else r.tags_of(x["desertionNo"])  # db 모드는 한 번에 읽어 둔다
            item = to_summary(x, tags)
            if query.tags:
                item.tag_match = tag_match(tags, query.tags)
            items.append(item)
        return AnimalPage(total=result.total, matched=result.matched, page=query.page, size=query.size, items=items)

    @app.get("/health", response_model=Health, response_model_by_alias=True)
    def health(r: AnimalRepository = Depends(repo)) -> Health:
        return Health(status="ok", data_mode=settings.data_mode.value, animal_count=r.count())

    @app.get("/animals", response_model=AnimalPage, response_model_by_alias=True)
    def list_animals(
        request: Request,
        query: AnimalQuery = Depends(filters),
        text: str | None = Query(
            None, max_length=200,
            description="문장으로 찾기 (예: 겁이 많지만 사람을 좋아하는 아이). 공고 글 벡터와 가까운 순. 거르지 않고 순서만 바꾼다",
        ),
        r: AnimalRepository = Depends(repo),
    ) -> AnimalPage:
        if text and text.strip():
            query = replace(query, vector=embed(r, "text", request.app.state.embedder.text, text))
        return page_of(r, query)

    @app.post(
        "/animals/photo-search", response_model=AnimalPage, response_model_by_alias=True,
        openapi_extra={"requestBody": {"required": True, "content": {
            t: {"schema": {"type": "string", "format": "binary"}} for t in IMAGE_TYPES}}},
    )
    async def photo_search(request: Request, query: AnimalQuery = Depends(filters),
                           r: AnimalRepository = Depends(repo)) -> AnimalPage:
        """사진으로 찾기. 본문에 사진 파일을 그대로 보낸다(Content-Type: image/*). 필터는 GET /animals 와 같다.
        공고마다 가장 닮은 사진 칸의 거리로 줄 세운다."""
        data = await request.body()
        if not data:
            raise HTTPException(status_code=422, detail="사진이 비어 있습니다.")
        vector = embed(r, "image", request.app.state.embedder.image, data)
        return page_of(r, replace(query, vector=vector))

    @app.get("/tags", response_model=TagCatalog, response_model_by_alias=True)
    def list_tags(
        species: Literal["dog", "cat"] = Query("dog", description="태그 세트를 볼 종. 고양이 태그 세트는 준비 중이라 비어 있다"),
        r: AnimalRepository = Depends(repo),
    ) -> TagCatalog:
        return TagCatalog(**catalog(species, r.tag_counts()))

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
        return to_detail(raw, raw["_tags"] if "_tags" in raw else r.tags_of(desertion_no))

    return app
