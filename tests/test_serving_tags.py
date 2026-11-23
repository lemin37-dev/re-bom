"""검색 API 사진 태그: /tags 사전·추천, /animals?tags= 순서(맞는 아이 먼저, 털이 반대인 아이만 뺌). 외부 호출 없음."""
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


def ranked(client, **params):
    """(공고번호, 단계) 를 응답 순서대로, 그리고 matched."""
    body = client.get("/animals", params={"size": 100, "state": "protect", **params}).json()
    return [(i["desertionNo"], i["tagMatch"]) for i in body["items"]], body["matched"]


def test_tags_rank_matches_first_and_keep_the_rest(client):
    a, b, c, d = PROTECTED_DOGS[:4]
    rows, matched = ranked(client, tags="ear.drop")
    order = [m for _, m in rows]
    assert dict(rows)[a] == dict(rows)[c] == "all" and matched == 2
    assert dict(rows)[d] == "unknown"  # 귀 태그가 없음
    assert dict(rows)[b] == "other"  # 귀 태그가 다름 (빼지 않는다)
    assert order == sorted(order, key=["all", "some", "unknown", "other"].index)
    assert len(rows) == client.get("/animals", params={"state": "protect"}).json()["total"]  # 아무도 빠지지 않았다


def test_some_categories_match(client):
    a, b, c, d = PROTECTED_DOGS[:4]
    rows, matched = ranked(client, tags="ear.drop,eye.round")
    got = dict(rows)
    assert matched == 0
    assert got[a] == got[c] == got[d] == "some" and got[b] == "other"


def test_opposite_fur_is_the_only_exclusion(client):
    a, b, c, d = PROTECTED_DOGS[:4]
    got = dict(ranked(client, tags="fur.fluffy,ear.drop")[0])
    assert c not in got  # 짧은 털이라 뺀다
    assert got[a] == "all" and got[b] == "some" and got[d] == "unknown"
    got = dict(ranked(client, tags="fur.fluffy,fur.short")[0])  # 털을 둘 다 고르면 아무도 빠지지 않는다
    assert {got[a], got[b], got[c]} == {"all"}


def test_untagged_and_other_species_stay_after_matches(client):
    a, _, c, _ = PROTECTED_DOGS[:4]
    body = client.get("/animals", params={"size": 100, "tags": "ear.drop"}).json()
    assert [i["desertionNo"] for i in body["items"][:2]] == sorted([a, c], key=[i["desertionNo"] for i in body["items"]].index)
    assert {i["desertionNo"] for i in body["items"][:2]} == {a, c}
    cats = client.get("/animals", params={"size": 100, "tags": "ear.drop", "species": "cat"}).json()
    assert cats["total"] > 0 and {i["tagMatch"] for i in cats["items"]} == {"unknown"} and cats["matched"] == 0


def test_without_tags_no_match_fields(client):
    body = client.get("/animals", params={"size": 3}).json()
    assert body["matched"] is None and {i["tagMatch"] for i in body["items"]} == {None}


def test_unknown_or_hidden_tag_is_rejected(client):
    for tags in ("ear.flat", "breed.진도견"):
        res = client.get("/animals", params={"tags": tags})
        assert res.status_code == 422 and tags in res.json()["detail"]


def test_without_tag_file_search_still_works(tmp_path):
    shutil.copy(DEFAULT_SAMPLE_PATH, tmp_path / DEFAULT_SAMPLE_PATH.name)
    client = TestClient(create_app(Settings(data_mode=DataMode.SAMPLE, sample_path=tmp_path / DEFAULT_SAMPLE_PATH.name,
                                            _env_file=None)))
    assert client.get("/tags").json()["recommended"] == []
    body = client.get("/animals", params={"tags": "ear.drop"}).json()
    assert body["matched"] == 0 and body["total"] == client.get("/animals").json()["total"] > 0


def test_real_sample_tags_match_catalog_counts():
    client = TestClient(create_app(Settings(data_mode=DataMode.SAMPLE, _env_file=None)))
    items = json.loads(SAMPLE_TAGS.read_text(encoding="utf-8"))["items"]
    body = client.get("/tags").json()
    for tag in (t for c in body["categories"] for t in c["tags"]):
        expected = sum(1 for i in items if tag["code"] in i["tags"])
        assert tag["count"] == expected
        matched = client.get("/animals", params={"tags": tag["code"], "state": "protect"}).json()["matched"]
        assert matched == expected
    assert len(body["recommended"]) == 4


def test_animals_carry_their_photo_tags_as_chips(client):
    a, b, _, _ = PROTECTED_DOGS[:4]
    items = {i["desertionNo"]: i for i in client.get("/animals", params={"size": 100}).json()["items"]}
    assert items[a]["photoTags"] == [{"code": "fur.fluffy", "label": "복슬복슬"}, {"code": "ear.drop", "label": "늘어진 귀"}]
    assert [t["code"] for t in items[b]["photoTags"]] == ["fur.fluffy", "ear.semi"]  # 품종 태그는 칩에 없다
    untagged = next(i for no, i in items.items() if no not in PROTECTED_DOGS[:4])
    assert untagged["photoTags"] == []
    assert client.get(f"/animals/{a}").json()["photoTags"] == items[a]["photoTags"]
