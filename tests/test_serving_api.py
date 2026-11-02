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


def test_db_mode_not_ready():
    with pytest.raises(NotImplementedError):
        build_repository(Settings(data_mode=DataMode.DB, _env_file=None))


def test_mode_from_env(monkeypatch):
    monkeypatch.setenv("REBOM_DATA_MODE", "sample")
    assert Settings(_env_file=None).data_mode is DataMode.SAMPLE
    monkeypatch.setenv("REBOM_DATA_MODE", "db")
    assert Settings(_env_file=None).data_mode is DataMode.DB
