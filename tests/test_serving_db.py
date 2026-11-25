"""검색 API db 모드를 실제 PostgreSQL 에 돌려 본다. REBOM_TEST_PG_DSN 이 있을 때만 돈다.

⚠ 그 DB 의 public 스키마를 지우고 다시 만든다(test_serving_sync_db.py 와 같음). 운영 DB 를 넣지 말 것.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from fastapi.testclient import TestClient  # noqa: E402

from rebom.common.config import DataMode, Settings  # noqa: E402
from rebom.serving import db_repository  # noqa: E402
from rebom.serving.app import create_app  # noqa: E402

DSN = os.environ.get("REBOM_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="REBOM_TEST_PG_DSN 없음")
MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
TODAY = date(2026, 11, 25)


def _notice(no, **kw):
    base = {"desertion_no": no, "notice_no": f"N-{no}", "status_group": "PROTECTING", "process_state_raw": "보호중",
            "species_code": "417000", "species_name": "개", "breed_name_raw": "믹스견", "breed_full_name_raw": "[개] 믹스견",
            "sex_code": "F", "neuter_code": "Y", "birth_year": 2023, "weight_raw": "5(Kg)", "weight_kg": 5,
            "color_raw": "갈색", "special_mark": "온순함", "happen_date": date(2026, 11, 20), "happen_place": "공원",
            "org_name": "서울특별시 마포구", "notice_start_date": date(2026, 11, 20), "notice_end_date": date(2026, 11, 30),
            "care_reg_no": "311", "care_name": "마포 보호소", "care_tel": "02-000", "care_address": "서울특별시 마포구 1",
            "content_hash": "a" * 64}
    base.update(kw)
    return base


@pytest.fixture()
def client():
    c = psycopg.connect(DSN, autocommit=True)
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public; CREATE DOMAIN vector AS text")
    for name in ("003_service_tables.sql", "004_vector_tables_v01.sql", "005_schema_migrations.sql",
                 "006_embedding_drop_config_columns.sql", "007_image_embedding_drop_photo_run_id.sql"):
        c.execute((MIGRATIONS / name).read_text(encoding="utf-8").replace("vector(768)", "vector"))
    now = datetime.now(timezone.utc)
    rows = [
        _notice("1001"),
        _notice("1002", sex_code="M", birth_year=2016, weight_kg=12, happen_date=date(2026, 11, 22),
                special_mark="겁이 많음", care_reg_no="411", care_name="수원 보호소", care_address="경기도 수원시 2",
                org_name="경기도 수원시"),
        _notice("1003", species_code="422400", species_name="고양이", happen_date=date(2026, 11, 21)),
        # 종료 뒤 30일 안: 보임 / 지남: 안 보임
        _notice("1004", status_group="CLOSED", process_state_raw="종료(입양)", closed_transition_at=now - timedelta(days=3),
                visible_until=now + timedelta(days=27), happen_date=date(2026, 11, 1)),
        _notice("1005", status_group="CLOSED", process_state_raw="종료(입양)", closed_transition_at=now - timedelta(days=40),
                visible_until=now - timedelta(days=10)),
    ]
    cols = list(rows[0]) + ["closed_transition_at", "visible_until"]
    with c.cursor() as cur:
        cur.executemany(f"INSERT INTO service_notice ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                        [tuple(r.get(k) for k in cols) for r in rows])
        cur.executemany("INSERT INTO service_notice_photo (desertion_no, image_slot, sha256, photo_version, serving_s3_key, "
                        "thumb_s3_key) VALUES (%s, %s, %s, 1, %s, %s)",
                        [("1001", 2, "b" * 64, "images/1001/2.webp", None),
                         ("1001", 1, "a" * 64, "images/1001/1.webp", "images/1001/1_thumb.webp")])
        cur.executemany("INSERT INTO service_notice_tag (desertion_no, subject_type, subject_key, task, tag_code, tag_label, "
                        "version, source, searchable, displayable) VALUES (%s, 'photo', %s, 'photo_trait', %s, %s, "
                        "'photo_trait@1', 'photo', %s, true)",
                        [("1001", "a" * 64, "ear.drop", "늘어진 귀", True), ("1001", "a" * 64, "fur.fluffy", "복슬복슬", True),
                         ("1002", "c" * 64, "fur.short", "짧은 털", True), ("1002", "c" * 64, "eye.round", "둥근 눈매", False)])
    c.close()
    app = create_app(Settings(data_mode=DataMode.DB, db_dsn=DSN, image_base_url="https://cdn.example", _env_file=None))
    app.state.repo._today = lambda: TODAY
    yield TestClient(app)


def ids(body):
    return [i["desertionNo"] for i in body["items"]]


def test_visible_rows_and_health(client):
    assert client.get("/health").json() == {"status": "ok", "dataMode": "db", "animalCount": 4}
    body = client.get("/animals").json()
    assert body["total"] == 4 and ids(body) == ["1002", "1003", "1001", "1004"]  # 발견일 최신순, 30일 지난 종료 제외


def test_summary_fields_from_clean_columns(client):
    item = next(i for i in client.get("/animals").json()["items"] if i["desertionNo"] == "1001")
    assert item["photos"] == ["https://cdn.example/images/1001/1.webp", "https://cdn.example/images/1001/2.webp"]
    assert item["thumbnail"] == "https://cdn.example/images/1001/1_thumb.webp"
    assert (item["ageYears"], item["ageLabel"], item["weightKg"], item["isProtected"]) == (3, "3살", 5.0, True)
    assert item["happenDate"] == "20261120" and item["shelter"]["sido"] == "서울특별시"
    assert [t["code"] for t in item["photoTags"]] == ["fur.fluffy", "ear.drop"]
    assert not any("owner" in k.lower() for k in item)


def test_filters(client):
    get = lambda **p: ids(client.get("/animals", params=p).json())  # noqa: E731
    assert get(species="cat") == ["1003"]
    assert get(state="ended") == ["1004"]
    assert get(state="protect", sex="M") == ["1002"]
    assert get(region="경기") == ["1002"]
    assert get(shelter="311", state="protect") == ["1003", "1001"]
    assert get(q="겁이 온순") == ["1002", "1003", "1001", "1004"]
    assert get(q="겁이") == ["1002"]
    assert get(age_min=5) == ["1002"]
    assert get(weight_lt=10) == ["1003", "1001", "1004"]
    assert get(sort="notice_end", species="dog") == ["1001", "1002", "1004"]


def test_tags_rank_not_filter(client):
    body = client.get("/animals", params={"tags": "ear.drop,fur.fluffy", "species": "dog"}).json()
    # 1001 모두 맞음, 1004 태그 없음(unknown), 1002 털 반대(짧은 털)라 빠짐
    assert ids(body) == ["1001", "1004"] and body["matched"] == 1 and body["total"] == 2
    assert body["items"][0]["tagMatch"] == "all"
    counts = {t["code"]: t["count"] for c in client.get("/tags").json()["categories"] for t in c["tags"]}
    assert counts["ear.drop"] == 1 and counts["fur.short"] == 1 and counts["eye.round"] == 0  # searchable 만


def test_detail_shelters_and_missing(client):
    d = client.get("/animals/1004").json()
    assert d["isProtected"] is False and d["state"] == "종료(입양)"
    assert client.get("/animals/1005").status_code == 404  # 보관 기간 지난 종료 공고
    shelters = client.get("/shelters").json()["items"]
    assert [(s["regNo"], s["animalCount"]) for s in shelters] == [("311", 2), ("411", 1)]


def test_reconnects_after_connection_drop(client):
    repo = client.app.state.repo
    client.get("/health")
    repo._conn.close()
    assert client.get("/health").json()["animalCount"] == 4


def test_age_of():
    assert db_repository.age_of(2026, TODAY) == 0 and db_repository.age_of(None, TODAY) is None
