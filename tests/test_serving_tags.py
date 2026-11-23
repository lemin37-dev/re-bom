"""검색 API 사진 태그: /tags 사전·추천, /animals?tags= 조건(범주 안 OR, 범주끼리 AND). 외부 호출 없음."""
from __future__ import annotations

import json
import shutil

import pytest
from fastapi.testclient import TestClient

from rebom.common.config import DEFAULT_SAMPLE_PATH, DataMode, Settings
from rebom.serving.app import create_app

SAMPLE_TAGS = DEFAULT_SAMPLE_PATH.with_name("photo_tags.json")
PROTECTED_DOGS = [
    r["desertionNo"] for r in json.loads(DEFAULT_SAMPLE_PATH.read_text(encoding="utf-8"))
    if r.get("upKindCd") == "417000" and r.get("processState") == "보호중"
]


def app_with_tags(tmp_path, items):
    shutil.copy(DEFAULT_SAMPLE_PATH, tmp_path / DEFAULT_SAMPLE_PATH.name)
    (tmp_path / "photo_tags.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    return TestClient(create_app(Settings(data_mode=DataMode.SAMPLE, sample_path=tmp_path / DEFAULT_SAMPLE_PATH.name,
                                          _env_file=None)))


@pytest.fixture()
def client(tmp_path):
    a, b, c, d = PROTECTED_DOGS[:4]
    return app_with_tags(tmp_path, [
        {"desertionNo": a, "tags": ["ear.drop", "fur.fluffy"]},
        {"desertionNo": b, "tags": ["ear.semi", "fur.fluffy", "breed.진도견"]},
        {"desertionNo": c, "tags": ["ear.drop", "fur.short"]},
        {"desertionNo": d, "tags": ["eye.round"]},
    ])


def ids(client, **params):
    body = client.get("/animals", params={"size": 100, **params}).json()
    return {i["desertionNo"] for i in body["items"]}


def test_catalog_groups_detail_tags_under_categories_without_breed(client):
    body = client.get("/tags").json()
    assert [c["key"] for c in body["categories"]] == ["fur", "ear", "eye"]
    ear = next(c for c in body["categories"] if c["key"] == "ear")
    assert ear["label"] == "귀 모양"
    assert [(t["code"], t["label"], t["count"]) for t in ear["tags"]] == [
        ("ear.drop", "늘어진 귀", 2), ("ear.semi", "반쯤 선 귀", 1), ("ear.prick", "선 귀", 0)]
    codes = {t["code"] for c in body["categories"] for t in c["tags"]}
    assert not any(code.startswith("breed.") for code in codes)


def test_recommended_are_most_common_tags_with_results(client):
    assert client.get("/tags").json()["recommended"] == ["fur.fluffy", "ear.drop", "fur.short", "ear.semi"]


def test_tags_or_within_category_and_across_categories(client):
    a, b, c, _ = PROTECTED_DOGS[:4]
    assert ids(client, tags="ear.drop") == {a, c}
    assert ids(client, tags="ear.drop,ear.semi") == {a, b, c}
    assert ids(client, tags="ear.drop,ear.semi,fur.fluffy") == {a, b}
    assert ids(client, tags="ear.drop,eye.round") == set()


def test_tags_combine_with_other_conditions(client):
    a, _, c, _ = PROTECTED_DOGS[:4]
    assert ids(client, tags="ear.drop", species="dog", state="protect") == {a, c}
    assert ids(client, tags="ear.drop", species="cat") == set()


def test_unknown_or_hidden_tag_is_rejected(client):
    for tags in ("ear.flat", "breed.진도견"):
        res = client.get("/animals", params={"tags": tags})
        assert res.status_code == 422 and tags in res.json()["detail"]


def test_without_tag_file_search_still_works(tmp_path):
    shutil.copy(DEFAULT_SAMPLE_PATH, tmp_path / DEFAULT_SAMPLE_PATH.name)
    client = TestClient(create_app(Settings(data_mode=DataMode.SAMPLE, sample_path=tmp_path / DEFAULT_SAMPLE_PATH.name,
                                            _env_file=None)))
    assert client.get("/tags").json()["recommended"] == []
    assert client.get("/animals", params={"tags": "ear.drop"}).json()["total"] == 0
    assert client.get("/animals").json()["total"] > 0


def test_real_sample_tags_match_catalog_counts():
    client = TestClient(create_app(Settings(data_mode=DataMode.SAMPLE, _env_file=None)))
    items = json.loads(SAMPLE_TAGS.read_text(encoding="utf-8"))["items"]
    body = client.get("/tags").json()
    for tag in (t for c in body["categories"] for t in c["tags"]):
        expected = sum(1 for i in items if tag["code"] in i["tags"])
        assert tag["count"] == expected
        total = client.get("/animals", params={"tags": tag["code"], "state": "protect"}).json()["total"]
        assert total == expected
    assert len(body["recommended"]) == 4
