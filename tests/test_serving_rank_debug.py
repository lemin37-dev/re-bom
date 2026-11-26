"""dev 모드(REBOM_ENV=dev) 검색 순위 근거. prod(기본)에서는 rank·debug 키가 응답에 없어야 한다. 외부 호출 없음."""
from __future__ import annotations

import json
import shutil
from datetime import date

import pytest
from fastapi.testclient import TestClient

from rebom.common.config import DEFAULT_SAMPLE_PATH, AppEnv, DataMode, Settings
from rebom.serving import db_repository
from rebom.serving.app import create_app
from rebom.serving.tags import parse_tags, tag_detail

SAMPLE = json.loads(DEFAULT_SAMPLE_PATH.read_text(encoding="utf-8"))
PROTECTED_DOGS = [r["desertionNo"] for r in SAMPLE if r.get("upKindCd") == "417000" and r.get("processState") == "보호중"]


def sample_client(tmp_path, env: AppEnv) -> TestClient:
    shutil.copy(DEFAULT_SAMPLE_PATH, tmp_path / DEFAULT_SAMPLE_PATH.name)
    a, b, c, d = PROTECTED_DOGS[:4]
    items = [
        {"desertionNo": a, "tags": ["ear.drop", "fur.fluffy"]},
        {"desertionNo": b, "tags": ["ear.semi", "fur.fluffy"]},
        {"desertionNo": c, "tags": ["ear.drop", "fur.short"]},
        {"desertionNo": d, "tags": ["eye.round"]},
    ]
    (tmp_path / "photo_tags.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    settings = Settings(data_mode=DataMode.SAMPLE, sample_path=tmp_path / DEFAULT_SAMPLE_PATH.name, env=env,
                        _env_file=None)
    return TestClient(create_app(settings))


PARAMS = {"species": "dog", "state": "protect", "tags": "fur.fluffy,ear.drop", "size": 5}


def test_prod_is_default_and_hides_rank_keys(tmp_path):
    assert Settings(_env_file=None).env == AppEnv.PROD
    client = sample_client(tmp_path, AppEnv.PROD)
    body = client.get("/animals", params=PARAMS).json()
    assert "debug" not in body
    assert body["items"] and all("rank" not in i for i in body["items"])
    detail = client.get(f"/animals/{body['items'][0]['desertionNo']}").json()
    assert "rank" not in detail


def test_env_is_read_from_dotenv(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("REBOM_ENV=dev\n", encoding="utf-8")
    assert Settings(_env_file=env_file).rank_debug


def test_dev_explains_filters_tags_and_sort(tmp_path):
    a, b, c, d = PROTECTED_DOGS[:4]
    body = sample_client(tmp_path, AppEnv.DEV).get("/animals", params={**PARAMS, "size": 100}).json()
    items = body["items"]
    assert [i["rank"]["position"] for i in items] == list(range(1, len(items) + 1))
    first = items[0]["rank"]
    assert items[0]["desertionNo"] == a and first["tagMatch"] == "all"
    assert {f["name"]: f["condition"] for f in first["filters"]} == {"species": "dog", "state": "protect"}
    assert first["filters"][0]["value"] == "개 (417000)"
    assert [t["result"] for t in first["tags"]] == ["match", "match"]
    assert first["sortKey"] == "happenDate" and first["vector"] is None
    by_no = {i["desertionNo"]: i["rank"] for i in items}
    assert c not in by_no  # 털이 반대라 빠짐
    assert by_no[d]["tagMatch"] == "unknown" and {t["result"] for t in by_no[d]["tags"]} == {"unknown"}
    debug = body["debug"]
    assert debug["env"] == "dev" and debug["excluded"] == 1 and debug["vector"] is None
    assert debug["order"][0] == "필터(WHERE): species=dog, state=protect"
    assert debug["order"][-2].startswith("1순위 태그 단계") and debug["order"][-1] == "2순위 구조일 최신순"


def test_dev_sample_text_shows_matched_fields(tmp_path):
    word = next(w for w in (SAMPLE[0].get("specialMark") or "").split() if len(w) >= 2)
    body = sample_client(tmp_path, AppEnv.DEV).get("/animals", params={"text": word, "size": 3}).json()
    q = next(f for f in body["items"][0]["rank"]["filters"] if f["name"] == "q")
    assert q["condition"] == word and f"{word}→" in q["value"]
    assert any("단어 포함(q)" in line for line in body["debug"]["order"])


def test_tag_detail_reports_each_category():
    got = tag_detail(frozenset({"ear.semi", "fur.fluffy"}), parse_tags("fur.fluffy,ear.drop,eye.round"))
    assert [(t["category"], t["result"], t["have"]) for t in got] == [
        ("fur", "match", ["fur.fluffy"]), ("ear", "other", ["ear.semi"]), ("eye", "unknown", [])]
    assert got[0]["exclusive"] and not got[1]["exclusive"]


def test_openapi_still_describes_card_fields(tmp_path):
    schema = sample_client(tmp_path, AppEnv.PROD).get("/openapi.json").json()["components"]["schemas"]
    summary = schema.get("AnimalSummary") or schema["AnimalSummary-Output"]
    assert {"desertionNo", "similarity", "rank"} <= summary["properties"].keys()


# ---- db 모드: 벡터 거리·similarity ----

pytest.importorskip("PIL")
from test_serving_vector import DIM, FakeDb, FakeEmbedder  # noqa: E402


@pytest.fixture()
def db_app():
    db = FakeDb({"1001": 0.1, "1002": 0.4, "1003": None})
    app = create_app(Settings(data_mode=DataMode.DB, db_dsn="postgresql://unused", env=AppEnv.DEV, _env_file=None),
                     embedder_factory=FakeEmbedder)
    app.state.repo._rows = db.rows
    app.state.repo._today = lambda: date(2026, 11, 25)
    return app


def test_dev_db_text_search_shows_distance_and_model(db_app):
    body = TestClient(db_app).get("/animals", params={"text": "겁 많은 아이"}).json()
    vecs = [i["rank"]["vector"] for i in body["items"]]
    assert [(v["distance"], v["similarity"]) for v in vecs] == [(0.1, 0.9), (0.4, 0.6), (None, None)]
    assert vecs[0] | {"distance": None, "similarity": None} == {
        "kind": "text", "version": "text_embedding@3", "model": "gemini-embedding-2", "distance": None, "similarity": None}
    debug = body["debug"]
    assert debug["dataMode"] == "db" and debug["vector"] == {
        "kind": "text", "version": "text_embedding@3", "model": "gemini-embedding-2", "dim": DIM}
    assert debug["order"][1].startswith("1순위 글 벡터 코사인 거리") and "임계값 없음" in debug["order"][1]
