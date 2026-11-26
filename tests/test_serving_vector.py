"""글·사진 벡터 검색. Gemini·RDS 는 부르지 않고 가짜 임베더와 가짜 연결을 쓴다."""
from __future__ import annotations

import io
import statistics
from datetime import date

import pytest

pytest.importorskip("PIL")
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from rebom.common.config import DataMode, Settings  # noqa: E402
from rebom.serving import db_repository  # noqa: E402
from rebom.serving.app import create_app  # noqa: E402
from rebom.serving.db_repository import DbRepository  # noqa: E402
from rebom.serving.repository import AnimalQuery, QueryVector, VectorSpec  # noqa: E402
from rebom.serving.tags import parse_tags  # noqa: E402

DIM = 4
NOTICE = {c: None for c in db_repository.NOTICE_FIELDS} | {
    "status_group": "PROTECTING", "species_code": "417000", "species_name": "개"}


class FakeEmbedder:
    def __init__(self, dim):
        self.dim, self.texts, self.images = dim, [], []

    def embed_texts(self, texts):
        self.texts += texts
        return [[0.1] * self.dim for _ in texts]

    def embed_image(self, data):
        self.images.append(data)
        return [0.2] * self.dim


class FakeDb:
    """SQL 을 받아 미리 정한 행을 돌려준다. 자리표시자 수와 값 수가 다르면 실패한다."""

    def __init__(self, dist):
        self.dist = dist  # desertion_no → 거리 (None 이면 벡터 없음). 넣은 순서가 거리 순
        self.calls = []
        qc = {"model_id": "gemini-embedding-2", "dim": DIM}
        self.active = {"text_embedding": ("text_embedding@3", qc), "image_embedding": ("image_embedding@1", qc)}

    def rows(self, sql, args=()):
        assert sql.count("%s") == len(args), sql
        self.calls.append((sql, list(args)))
        if "FROM active_version" in sql:
            row = self.active.get(args[0])
            return [{"version": row[0], "query_config": row[1]}] if row else []
        if sql.startswith("WITH d AS"):
            dists = sorted(d for d in self.dist.values() if d is not None)
            nth = args[-1]
            return [{"scored": len(dists), "missing": len(self.dist) - len(dists),
                     "top": 1 - dists[0] if dists else None, "median": 1 - statistics.median(dists) if dists else None,
                     "nth": 1 - dists[nth] if len(dists) > nth else None}]
        if sql.startswith("SELECT count(*)"):
            return [{"n": len(self.dist)}]
        if "AS dist" in sql:
            has_vec = "v.dist" in sql
            hits = [{"desertion_no": no, "dist": d if has_vec else None} for no, d in self.dist.items()]
            return hits[args[-1]:args[-1] + args[-2]] if sql.endswith("LIMIT %s OFFSET %s") else hits
        if "FROM service_notice n WHERE n.desertion_no = ANY" in sql:
            return [NOTICE | {"desertion_no": no} for no in args[0]]
        return []


@pytest.fixture()
def db():
    return FakeDb({"1001": 0.1, "1002": 0.4, "1003": None})


@pytest.fixture()
def app(db):
    a = create_app(Settings(data_mode=DataMode.DB, db_dsn="postgresql://unused", _env_file=None),
                   embedder_factory=FakeEmbedder)
    a.state.repo._rows = db.rows
    a.state.repo._today = lambda: date(2026, 11, 25)
    return a


def _jpeg(color=(200, 50, 50), noise=True):
    img = Image.new("RGB", (64, 64), color)
    if noise:
        for x in range(0, 64, 4):
            for y in range(0, 64, 4):
                img.putpixel((x, y), ((x * 7) % 255, (y * 11) % 255, 0))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def test_text_search_embeds_query_and_ranks_by_similarity(app, db):
    body = TestClient(app).get("/animals", params={"text": "겁 많은 아이", "species": "dog"}).json()
    assert [i["desertionNo"] for i in body["items"]] == ["1001", "1002", "1003"]
    assert [i["similarity"] for i in body["items"]] == [0.9, 0.6, None]
    embedder = app.state.embedder._by_dim[DIM]
    assert embedder.texts == ["task: search result | query: 겁 많은 아이"]
    sql, args = next(c for c in db.calls if "AS dist" in c[0])
    assert "FROM text_embedding t" in sql and "v.dist ASC NULLS LAST" in sql
    assert args[:3] == ["[0.1,0.1,0.1,0.1]", "text_embedding@3", "417000"]


def test_without_text_keeps_plain_listing(app, db):
    body = TestClient(app).get("/animals").json()
    assert all(i["similarity"] is None for i in body["items"])
    assert not any("embedding" in sql for sql, _ in db.calls)


def test_photo_search_uses_current_photo_slots(app, db):
    r = TestClient(app).post("/animals/photo-search", params={"size": 2}, content=_jpeg(),
                             headers={"Content-Type": "image/png"})
    assert r.status_code == 200, r.text
    assert [i["desertionNo"] for i in r.json()["items"]] == ["1001", "1002"]
    sent = app.state.embedder._by_dim[DIM].images[0]
    assert sent[:3] == b"\xff\xd8\xff"  # Gold 와 같은 JPEG 전처리
    sql, args = next(c for c in db.calls if "AS dist" in c[0])
    assert "p.sha256 = e.sha256" in sql and args[1] == "image_embedding@1"


@pytest.mark.parametrize("data,msg", [(b"not an image", "사진만"), (_jpeg((255, 255, 255), noise=False), "빈 사진")])
def test_photo_search_rejects_bad_images(app, data, msg):
    r = TestClient(app).post("/animals/photo-search", content=data, headers={"Content-Type": "image/png"})
    assert r.status_code == 422 and msg in r.json()["detail"]


def test_tags_rank_first_then_similarity(app, db):
    db.dist = {"1001": 0.1, "1002": 0.2}
    repo = app.state.repo
    repo._tags = lambda nos: {"1002": frozenset({"ear.drop"})}
    vec = QueryVector(VectorSpec("text", "text_embedding@3", "gemini-embedding-2", DIM), (0.1,) * DIM)
    page = repo.search(AnimalQuery(tags=parse_tags("ear.drop"), vector=vec))
    assert [r["desertionNo"] for r in page.rows] == ["1002", "1001"]
    assert [r["_similarity"] for r in page.rows] == [0.8, 0.9]


def test_no_active_version_is_503(app, db):
    db.active = {}
    r = TestClient(app).get("/animals", params={"text": "온순한 아이"})
    assert r.status_code == 503 and "활성 버전" in r.json()["detail"]


def test_embedding_failure_is_503_without_details(app):
    class Broken(FakeEmbedder):
        def embed_texts(self, texts):
            raise RuntimeError("secret detail")

    app.state.embedder._by_dim[DIM] = Broken(DIM)
    r = TestClient(app).get("/animals", params={"text": "온순한 아이"})
    assert r.status_code == 503 and "secret" not in r.text


def test_sample_mode_text_falls_back_to_word_match():
    client = TestClient(create_app(Settings(data_mode=DataMode.SAMPLE, _env_file=None), embedder_factory=FakeEmbedder))
    by_text = client.get("/animals", params={"text": "온순"}).json()
    assert by_text == client.get("/animals", params={"q": "온순"}).json()


def test_closed_notices_get_no_vector_rank(app, db):
    TestClient(app).get("/animals", params={"text": "온순한 아이"})
    sql = next(sql for sql, _ in db.calls if "AS dist" in sql)
    assert "ON v.desertion_no = n.desertion_no AND n.status_group = 'PROTECTING'" in sql


def test_missing_query_config_asks_a_human(app, db):
    db.active["text_embedding"] = ("text_embedding@3", None)
    r = TestClient(app).get("/animals", params={"text": "온순한 아이"})
    assert r.status_code == 503
    assert "python -m rebom.serving.sync text_vector" in r.json()["detail"]


def test_other_model_is_not_embedded(app, db):
    db.active["text_embedding"] = ("text_embedding@9", {"model_id": "other-model", "dim": DIM})
    r = TestClient(app).get("/animals", params={"text": "온순한 아이"})
    assert r.status_code == 503 and "other-model" in r.json()["detail"]
    assert app.state.embedder._by_dim == {}


def test_unreadable_key_asks_a_human(db):
    def no_key(dim):
        raise RuntimeError("Secrets Manager rebom/embed/prod/gemini_api_key 를 읽지 못했습니다: AccessDenied")

    app = create_app(Settings(data_mode=DataMode.DB, db_dsn="postgresql://unused", _env_file=None), embedder_factory=no_key)
    app.state.repo._rows = db.rows
    r = TestClient(app).get("/animals", params={"text": "온순한 아이"})
    assert r.status_code == 503 and "사람에게" in r.json()["detail"]


def test_livez_does_not_touch_db(app, db):
    assert TestClient(app).get("/livez").json() == {"status": "ok"}
    assert db.calls == []


def test_query_config_comes_from_registry_model_and_dim():
    from rebom.serving.sync.plan import query_config

    assert query_config({"model_id": "gemini-embedding-2", "dim": 768, "template_version": "A2"}) == \
        {"model_id": "gemini-embedding-2", "dim": 768}
    assert query_config({"model_id": "gemini-embedding-2"}) is None


def test_repository_without_vector_query_has_no_join():
    repo = DbRepository(lambda: None)
    db = FakeDb({"1001": None})
    repo._rows = db.rows
    repo.search(AnimalQuery())
    assert all("LEFT JOIN" not in sql for sql, _ in db.calls)
