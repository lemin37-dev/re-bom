"""검색 API sample 모드 테스트. 외부 API 는 호출하지 않고 data/sample/ 샘플만 쓴다."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from rebom.common.config import DEFAULT_SAMPLE_PATH, DataMode, Settings
from rebom.serving.app import create_app
from rebom.serving.repository import build_repository

SAMPLE = json.loads(DEFAULT_SAMPLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(Settings(data_mode=DataMode.SAMPLE, _env_file=None)))


def test_health_reports_sample_mode(client):
    body = client.get("/health").json()
    assert body == {"status": "ok", "dataMode": "sample", "animalCount": len(SAMPLE)}


def test_list_paginates(client):
    body = client.get("/animals", params={"size": 5, "page": 2}).json()
    assert body["total"] == len(SAMPLE)
    assert body["page"] == 2 and len(body["items"]) == 5
    item = body["items"][0]
    assert {"desertionNo", "photos", "thumbnail", "shelter", "isProtected"} <= item.keys()


def test_list_sorted_by_happen_date_desc(client):
    items = client.get("/animals", params={"size": 100}).json()["items"]
    dates = [i["happenDate"] for i in items]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.parametrize("species,name", [("dog", "개"), ("cat", "고양이")])
def test_filter_species(client, species, name):
    body = client.get("/animals", params={"species": species, "size": 100}).json()
    assert body["total"] == sum(1 for r in SAMPLE if r.get("upKindNm") == name)
    assert all(i["species"] == name for i in body["items"])


def test_filter_state(client):
    protect = client.get("/animals", params={"state": "protect"}).json()["total"]
    ended = client.get("/animals", params={"state": "ended"}).json()["total"]
    assert protect == sum(1 for r in SAMPLE if r["processState"] == "보호중")
    assert protect + ended == len(SAMPLE)


def test_filter_region_and_text(client):
    raw = SAMPLE[0]
    region = raw["careAddr"].split()[0]
    body = client.get("/animals", params={"region": region, "size": 100}).json()
    assert body["total"] >= 1
    assert all(region in f"{i['shelter']['addr']} {i['orgName']}" for i in body["items"])


def test_invalid_query_rejected(client):
    assert client.get("/animals", params={"species": "bird"}).status_code == 422
    assert client.get("/animals", params={"size": 0}).status_code == 422


def test_detail_and_404(client):
    raw = next(r for r in SAMPLE if r.get("popfile3"))
    body = client.get(f"/animals/{raw['desertionNo']}").json()
    assert body["desertionNo"] == raw["desertionNo"]
    assert body["photos"][:3] == [raw["popfile1"], raw["popfile2"], raw["popfile3"]]
    assert body["thumbnail"] == raw["popfile1"]
    assert client.get("/animals/000").status_code == 404


def test_missing_fields_become_null(client):
    raw = next(r for r in SAMPLE if "endReason" not in r)
    body = client.get(f"/animals/{raw['desertionNo']}").json()
    assert body["endReason"] is None


def test_owner_name_and_rfid_never_exposed(tmp_path):
    row = {**SAMPLE[0], "careOwnerNm": "홍길동", "rfidCd": "410000000000000"}
    path = tmp_path / "sample.json"
    path.write_text(json.dumps([row, {"noDesertionNo": True}], ensure_ascii=False), encoding="utf-8")
    c = TestClient(create_app(Settings(sample_path=path, _env_file=None)))
    assert c.get("/health").json()["animalCount"] == 1  # desertionNo 없는 항목은 버림
    text = c.get("/animals").text + c.get(f"/animals/{row['desertionNo']}").text
    assert "홍길동" not in text and "careOwnerNm" not in text
    assert "410000000000000" not in text


def test_db_mode_builds_lazily():
    """db 모드 저장소는 만들 때 접속하지 않는다(첫 요청 때 붙음). 실제 조회는 tests/test_serving_db.py."""
    from rebom.serving.db_repository import DbRepository

    assert isinstance(build_repository(Settings(data_mode=DataMode.DB, db_dsn="host=nowhere", _env_file=None)), DbRepository)


def test_mode_from_env(monkeypatch):
    monkeypatch.setenv("REBOM_DATA_MODE", "sample")
    assert Settings(_env_file=None).data_mode is DataMode.SAMPLE
    monkeypatch.setenv("REBOM_DATA_MODE", "db")
    assert Settings(_env_file=None).data_mode is DataMode.DB


def test_region_multiple_tokens(client):
    body = client.get("/animals", params={"region": "서울,경기", "size": 100}).json()
    expected = sum(
        1
        for r in SAMPLE
        if any(t in f"{r['careAddr'].split()[0]} {r['orgNm'].split()[0]}" for t in ("서울", "경기"))
    )
    assert body["total"] == expected > 0


def test_age_and_weight_filters(client):
    items = client.get("/animals", params={"age_min": 2, "age_max": 7, "size": 100}).json()["items"]
    assert items and all(2 <= i["ageYears"] <= 7 for i in items)
    items = client.get("/animals", params={"weight_min": 8, "weight_lt": 15, "size": 100}).json()["items"]
    assert items and all(8 <= i["weightKg"] < 15 for i in items)
    items = client.get("/animals", params={"weight_min": 10, "weight_lt": 25, "species": "dog", "size": 100}).json()["items"]
    assert items and all(10 <= i["weightKg"] < 25 and i["size"] == "medium" for i in items)


def test_summary_has_birth_year_and_dog_size(client):
    for i in client.get("/animals", params={"size": 100}).json()["items"]:
        assert i["birthYear"] is None or str(i["birthYear"]) == i["ageText"][:4]
        if i["speciesCode"] != "417000" or i["weightKg"] is None:
            assert i["size"] is None


def test_text_query_matches_any_word(client):
    body = client.get("/animals", params={"q": "처음 키우는 고양이", "size": 100}).json()
    assert body["total"] > 0
    assert all("고양이" in (i["breed"] or "") or i["specialMark"] for i in body["items"])


def test_shelter_filter_and_list(client):
    shelters = client.get("/shelters").json()
    assert shelters["total"] == len({r["careRegNo"] for r in SAMPLE})
    top = shelters["items"][0]
    assert {"regNo", "name", "tel", "addr", "sido", "sigungu", "animalCount"} <= top.keys()
    counts = [s["animalCount"] for s in shelters["items"]]
    assert counts == sorted(counts, reverse=True)
    body = client.get("/animals", params={"shelter": top["regNo"], "state": "protect"}).json()
    assert body["total"] == top["animalCount"]


def test_notice_end_sort(client):
    items = client.get("/animals", params={"state": "protect", "sort": "notice_end", "size": 100}).json()["items"]
    ends = [i["noticeEnd"] for i in items]
    from datetime import date

    today = date.today().strftime("%Y%m%d")
    upcoming = [e for e in ends if e >= today]
    assert ends[: len(upcoming)] == sorted(upcoming)


def test_etc_species_breed_uses_full_name(client):
    items = client.get("/animals", params={"species": "etc", "size": 100}).json()["items"]
    assert items and all(i["breed"] and "기타축종" not in i["breed"] for i in items)


def test_places_count_matches_list_total(client):
    params = {"state": "protect", "species": "dog"}
    places = client.get("/animals/places", params=params).json()
    assert places["total"] == client.get("/animals", params={**params, "size": 1}).json()["total"]
    assert places["total"] == sum(p["count"] for p in places["items"])
    counts = [p["count"] for p in places["items"]]
    assert counts == sorted(counts, reverse=True)
    # 마릿수는 보호소 하나만 골라 찾은 수와 같다
    top = places["items"][0]
    assert client.get("/animals", params={**params, "shelter": top["regNo"], "size": 1}).json()["total"] == top["count"]


def test_shelter_accepts_several_reg_numbers(client):
    items = client.get("/animals/places", params={"state": "protect"}).json()["items"][:2]
    both = client.get("/animals", params={"state": "protect", "shelter": ",".join(p["regNo"] for p in items), "size": 1}).json()
    assert both["total"] == items[0]["count"] + items[1]["count"]


def test_places_route_is_not_an_animal_id(client):
    assert client.get("/animals/places").status_code == 200
