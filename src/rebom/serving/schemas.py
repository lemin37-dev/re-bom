"""검색 API 응답 모양. 공공 API 원본 필드를 화면용 이름으로 옮긴다.

보호소 대표자 실명(careOwnerNm)과 동물등록번호(rfidCd)는 응답에 넣지 않는다.
필드를 허용 목록으로만 옮기므로 원본에 새 필드가 생겨도 그대로 새어 나가지 않는다.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

PHOTO_KEYS = tuple(f"popfile{i}" for i in range(1, 9))


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Shelter(_Model):
    reg_no: str | None = None
    name: str | None = None
    tel: str | None = None
    addr: str | None = None


class AnimalSummary(_Model):
    desertion_no: str
    notice_no: str | None = None
    species: str | None = None  # 개 / 고양이 / 기타축종
    species_code: str | None = None
    breed: str | None = None
    color: str | None = None
    sex: str | None = None  # M / F / Q
    neutered: str | None = None  # Y / N / U
    age_text: str | None = None  # 원문 그대로 (예: "2026(60일미만)(년생)"). 파싱은 정제 단계에서
    weight_text: str | None = None  # 원문 그대로 (예: "0.8~0.9(Kg)")
    state: str | None = None  # 보호중 / 종료(입양) ...
    is_protected: bool
    special_mark: str | None = None
    thumbnail: str | None = None
    photos: list[str]
    happen_date: str | None = None
    happen_place: str | None = None
    notice_start: str | None = None
    notice_end: str | None = None
    org_name: str | None = None
    shelter: Shelter
    updated_at: str | None = None


class AnimalDetail(AnimalSummary):
    end_reason: str | None = None
    health_check: str | None = None
    vaccination_check: str | None = None
    sociality: str | None = None  # sfeSoci
    health_note: str | None = None  # sfeHealth
    etc_note: str | None = None  # etcBigo


class AnimalPage(_Model):
    total: int
    page: int
    size: int
    items: list[AnimalSummary]


class Health(_Model):
    status: str
    data_mode: str
    animal_count: int


def _photos(raw: dict[str, Any]) -> list[str]:
    return [raw[k] for k in PHOTO_KEYS if raw.get(k)]


def _summary_fields(raw: dict[str, Any]) -> dict[str, Any]:
    photos = _photos(raw)
    return {
        "desertion_no": raw["desertionNo"],
        "notice_no": raw.get("noticeNo"),
        "species": raw.get("upKindNm"),
        "species_code": raw.get("upKindCd"),
        "breed": raw.get("kindNm") or raw.get("kindFullNm"),
        "color": raw.get("colorCd"),
        "sex": raw.get("sexCd"),
        "neutered": raw.get("neuterYn"),
        "age_text": raw.get("age"),
        "weight_text": raw.get("weight"),
        "state": raw.get("processState"),
        "is_protected": raw.get("processState") == "보호중",
        "special_mark": raw.get("specialMark"),
        "thumbnail": photos[0] if photos else None,
        "photos": photos,
        "happen_date": raw.get("happenDt"),
        "happen_place": raw.get("happenPlace"),
        "notice_start": raw.get("noticeSdt"),
        "notice_end": raw.get("noticeEdt"),
        "org_name": raw.get("orgNm"),
        "shelter": Shelter(
            reg_no=raw.get("careRegNo"),
            name=raw.get("careNm"),
            tel=raw.get("careTel"),
            addr=raw.get("careAddr"),
        ),
        "updated_at": raw.get("updTm"),
    }


def to_summary(raw: dict[str, Any]) -> AnimalSummary:
    return AnimalSummary(**_summary_fields(raw))


def to_detail(raw: dict[str, Any]) -> AnimalDetail:
    return AnimalDetail(
        **_summary_fields(raw),
        end_reason=raw.get("endReason"),
        health_check=raw.get("healthChk"),
        vaccination_check=raw.get("vaccinationChk"),
        sociality=raw.get("sfeSoci"),
        health_note=raw.get("sfeHealth"),
        etc_note=raw.get("etcBigo"),
    )
