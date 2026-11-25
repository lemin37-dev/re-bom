"""검색 API 응답 모양. 공공 API 원본 필드를 화면용 이름으로 옮긴다.

보호소 대표자 실명(careOwnerNm)과 동물등록번호(rfidCd)는 응답에 넣지 않는다.
필드를 허용 목록으로만 옮기므로 원본에 새 필드가 생겨도 그대로 새어 나가지 않는다.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from rebom.serving.derive import age_label, age_years, sido, sigungu, weight_kg
from rebom.serving.tags import TagMatch, chips

PHOTO_KEYS = tuple(f"popfile{i}" for i in range(1, 9))


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Shelter(_Model):
    reg_no: str | None = None
    name: str | None = None
    tel: str | None = None
    addr: str | None = None
    sido: str | None = None
    sigungu: str | None = None


class ShelterSummary(Shelter):
    animal_count: int  # 보호중인 동물 수



class PhotoTag(_Model):
    code: str  # 예: ear.drop
    label: str  # 예: 늘어진 귀


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
    age_years: int | None = None  # 출생연도 기준 추정 나이 (sample 모드 임시 파싱)
    age_label: str | None = None  # "60일 미만", "1살 미만", "3살"
    weight_kg: float | None = None  # 범위는 평균 (sample 모드 임시 파싱)
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
    photo_tags: list[PhotoTag] = []  # 대표 사진을 보고 모델이 붙인 태그 (serving/tags.py)
    tag_match: TagMatch | None = None  # 태그를 골랐을 때만: all · some · unknown · other


class AnimalDetail(AnimalSummary):
    end_reason: str | None = None
    health_check: str | None = None
    vaccination_check: str | None = None
    sociality: str | None = None  # sfeSoci
    health_note: str | None = None  # sfeHealth
    etc_note: str | None = None  # etcBigo


class AnimalPage(_Model):
    total: int
    matched: int | None = None  # 태그를 골랐을 때 고른 범주가 모두 맞는 아이 수 (이 아이들이 맨 앞)
    page: int
    size: int
    items: list[AnimalSummary]


class ShelterList(_Model):
    total: int
    items: list[ShelterSummary]


class TagOption(_Model):
    code: str  # 예: ear.drop
    label: str  # 예: 늘어진 귀
    count: int  # 이 태그가 붙은 보호중 동물 수


class TagCategory(_Model):
    key: str  # fur / ear / eye
    label: str
    tags: list[TagOption]


class TagCatalog(_Model):
    species: str  # dog / cat
    categories: list[TagCategory]  # 이 종의 태그 세트. 아직 없는 종은 빈 목록
    recommended: list[str]  # 추천 태그 코드 (보호중 동물이 많은 순)


class Health(_Model):
    status: str
    data_mode: str
    animal_count: int


def _photos(raw: dict[str, Any]) -> list[str]:
    return [raw[k] for k in PHOTO_KEYS if raw.get(k)]


def _breed(raw: dict[str, Any]) -> str | None:
    """기타 축종은 kindNm 이 '기타축종' 이고 실제 이름은 kindFullNm('[기타축종] 토끼')에만 있다."""
    name = raw.get("kindNm")
    full = raw.get("kindFullNm") or ""
    if not name or name == "기타축종":
        return full.split("]", 1)[-1].strip() or name
    return name


def shelter_of(raw: dict[str, Any]) -> Shelter:
    addr = raw.get("careAddr")
    return Shelter(
        reg_no=raw.get("careRegNo"),
        name=raw.get("careNm"),
        tel=raw.get("careTel"),
        addr=addr,
        sido=sido(addr),
        sigungu=sigungu(addr),
    )


def _summary_fields(raw: dict[str, Any], tags: Iterable[str] = (), today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    # db 모드는 정제된 값을 _ 키로 넘긴다(원문 파싱 안 함). sample 모드는 원문에서 계산한다
    photos = raw["_photos"] if "_photos" in raw else _photos(raw)
    thumbnail = raw["_thumbnail"] if "_thumbnail" in raw else (photos[0] if photos else None)
    return {
        "desertion_no": raw["desertionNo"],
        "notice_no": raw.get("noticeNo"),
        "species": raw.get("upKindNm"),
        "species_code": raw.get("upKindCd"),
        "breed": _breed(raw),
        "color": raw.get("colorCd"),
        "sex": raw.get("sexCd"),
        "neutered": raw.get("neuterYn"),
        "age_text": raw.get("age"),
        "weight_text": raw.get("weight"),
        "age_years": raw["_ageYears"] if "_ageYears" in raw else age_years(raw.get("age"), today),
        "age_label": raw["_ageLabel"] if "_ageLabel" in raw else age_label(raw.get("age"), today),
        "weight_kg": raw["_weightKg"] if "_weightKg" in raw else weight_kg(raw.get("weight")),
        "state": raw.get("processState"),
        "is_protected": raw["_isProtected"] if "_isProtected" in raw else raw.get("processState") == "보호중",
        "special_mark": raw.get("specialMark"),
        "thumbnail": thumbnail,
        "photos": photos,
        "happen_date": raw.get("happenDt"),
        "happen_place": raw.get("happenPlace"),
        "notice_start": raw.get("noticeSdt"),
        "notice_end": raw.get("noticeEdt"),
        "org_name": raw.get("orgNm"),
        "shelter": shelter_of(raw),
        "updated_at": raw.get("updTm"),
        "photo_tags": chips(tags),
    }


def to_summary(raw: dict[str, Any], tags: Iterable[str] = ()) -> AnimalSummary:
    return AnimalSummary(**_summary_fields(raw, tags))


def to_detail(raw: dict[str, Any], tags: Iterable[str] = ()) -> AnimalDetail:
    return AnimalDetail(
        **_summary_fields(raw, tags),
        end_reason=raw.get("endReason"),
        health_check=raw.get("healthChk"),
        vaccination_check=raw.get("vaccinationChk"),
        sociality=raw.get("sfeSoci"),
        health_note=raw.get("sfeHealth"),
        etc_note=raw.get("etcBigo"),
    )
