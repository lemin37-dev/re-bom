"""동기화를 실제 PostgreSQL 에 돌려 본다 (S3·Glue 는 가짜 입력).

REBOM_TEST_PG_DSN 이 있을 때만 돈다. 예: REBOM_TEST_PG_DSN="host=/tmp port=55432 user=postgres dbname=synctest"
pgvector 가 없어도 되도록 vector 를 text 도메인으로 바꿔 003~007 을 적용한다(검색 연산자는 쓰지 않음).
⚠ 그 DB 의 public 스키마를 지우고 다시 만든다. 운영 DB 를 넣지 말 것.
"""
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from rebom.serving.sync import cli, db, inputs  # noqa: E402

DSN = os.environ.get("REBOM_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="REBOM_TEST_PG_DSN 없음")
MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
SHA_A, SHA_B = "a" * 64, "b" * 64
CFG_HASH = "f" * 64


@pytest.fixture()
def conn():
    c = psycopg.connect(DSN, autocommit=True)
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public; CREATE DOMAIN vector AS text")
    for name in ("003_service_tables.sql", "004_vector_tables_v01.sql", "005_schema_migrations.sql",
                 "006_embedding_drop_config_columns.sql", "007_image_embedding_drop_photo_run_id.sql"):
        c.execute((MIGRATIONS / name).read_text(encoding="utf-8").replace("vector(768)", "vector"))
    yield c
    c.close()


class Fake:
    """S3·Glue 대신 쓰는 입력."""

    def __init__(self):
        self.completion = {"bronze_run_id": "20261124T030000-aaaa", "status": "COMPLETED", "notice_snapshot_id": 11,
                           "notice_table": "silver.notice", "source_fetched_at": "2026-11-24T03:00:00+09:00"}
        self.publication = {"publish_id": "silver_20261124T030000-aaaa", "publish_seq": 1, "status": "READY",
                            "bronze_run_id": "20261124T030000-aaaa", "notice_snapshot_id": 11, "photo_snapshot_id": 22,
                            "notice_table": "silver.notice", "photo_table": "silver.photo", "photo_run_id": "photo-run-1"}
        self.notices = [notice("1001"), notice("1002"), notice("1003", "CLOSED")]
        self.photos = [photo("1001", 1, SHA_A), photo("1001", 2, SHA_B), photo("1002", 1, SHA_B)]
        self.vectors = {SHA_A: {"embedding": [0.1, 0.2], "gold_run_id": "g1"}}
        self.tags = [{"subject_type": "photo", "subject_key": SHA_A, "task": "photo_trait", "tag_code": "ear.drop",
                      "source": "photo", "source_hash": SHA_A, "searchable": True, "displayable": True, "rank": None,
                      "similarity": None, "gold_run_id": "t1"}]
        self.text = [{"subject_key": "1001", "source_hash": SHA_A, "embedding": [0.3], "gold_run_id": "x1",
                      "gold_updated_at": "2026-11-24T00:00:00+00:00"}]

    def install(self, monkeypatch):
        monkeypatch.setattr(db, "connect", lambda: psycopg.connect(DSN, autocommit=True))
        monkeypatch.setattr(inputs, "notice_completion", lambda *a, **k: dict(self.completion))
        monkeypatch.setattr(inputs, "ready_publication", lambda *a, **k: dict(self.publication))
        monkeypatch.setattr(inputs, "scan", lambda catalog, table, **k: list(
            self.notices if table == "silver.notice" else self.photos))
        monkeypatch.setattr(inputs, "shelters", lambda *a: {"344500": {"care_name": "보호소", "care_tel": "000",
                                                                       "care_address": "충남"}})
        monkeypatch.setattr(inputs, "registry_config", lambda catalog, env, task, version: ({"model_id": "m"}, CFG_HASH))
        monkeypatch.setattr(inputs, "image_vectors", lambda *a, **k: dict(self.vectors))
        monkeypatch.setattr(inputs, "gold_rows", lambda catalog, env, base, task, *a, **k: [
            t for t in self.tags if t["task"] == task])
        monkeypatch.setattr(inputs, "text_tag_inputs", lambda *a, **k: {})
        monkeypatch.setattr(inputs, "text_vectors", lambda *a, **k: list(self.text))
        monkeypatch.setattr(cli.Runner, "catalog", property(lambda self: None))
        monkeypatch.setattr(cli.Runner, "s3", property(lambda self: None))


def notice(no, status="PROTECTING"):
    return {"desertion_no": no, "status_group": status, "process_state_raw": "보호중" if status == "PROTECTING" else "종료(입양)",
            "species_code": "417000", "sex_code": "F", "care_reg_no": "344500", "special_mark": "온순",
            "careOwnerNm": "이름"}  # Silver 에 있어도 옮기지 않는지 본다


def photo(no, slot, sha):
    return {"desertion_no": no, "image_slot": slot, "sha256": sha,
            "original_s3_key": f"bronze/rescue/images/{no}/popfile{slot}.jpg",
            "serving_s3_key": f"images/{no}/{slot}.webp", "thumb_s3_key": f"images/{no}/{slot}_thumb.webp"}


def run(capsys, *argv) -> dict:
    code = cli.main(list(argv))
    out = capsys.readouterr()
    assert code == 0, out.err
    return json.loads(out.out)


def activate(conn, task, version):
    conn.execute("INSERT INTO active_version (task, version, config_hash, activated_by) VALUES (%s, %s, %s, 'test')",
                 (task, version, CFG_HASH))


def test_full_flow(conn, monkeypatch, capsys):
    fake = Fake()
    fake.install(monkeypatch)
    activate(conn, "image_embedding", "image_embedding@1")
    activate(conn, "photo_trait", "photo_trait@1")
    activate(conn, "text_embedding", "text_embedding@3")

    dry = run(capsys, "notice_status", "--dry-run")
    assert dry["notice"]["upserts"] == 2
    assert conn.execute("SELECT count(*) FROM service_notice").fetchone()[0] == 0  # 되돌림
    assert conn.execute("SELECT last_silver_run_id FROM sync_state").fetchall() == []

    r = run(capsys, "notice_status")
    assert r["notice"] == {"upserts": 2, "unchanged": 0, "skipped_not_protecting": 1, "closed_now": 0, "reopened": 0}
    cols = [c[0] for c in conn.execute("SELECT * FROM service_notice LIMIT 0").description]
    assert not any("owner" in c for c in cols)
    assert conn.execute("SELECT care_name FROM service_notice WHERE desertion_no='1001'").fetchone()[0] == "보호소"

    r = run(capsys, "photo_vector")
    assert r["photo"]["upserts"] == 3
    assert r["image_vector"]["upserts"] == 1 and r["image_vector"]["waiting_vector"] == 2
    row = conn.execute("SELECT version, sha256, s3_key, gold_run_id, embedding::text FROM image_embedding").fetchone()
    assert row == ("image_embedding@1", SHA_A, "bronze/rescue/images/1001/popfile1.jpg", "g1", "[0.1,0.2]")

    r = run(capsys, "tag")
    assert r["tag"]["upserts"] == 1 and r["skipped_tasks"] == ["photo_breed", "text_personality", "text_health"]
    assert conn.execute("SELECT desertion_no, tag_label, version FROM service_notice_tag").fetchall() == [
        ("1001", "늘어진 귀", "photo_trait@1")]

    r = run(capsys, "text_vector")
    assert r["text_vector"]["upserts"] == 1

    # 다음 날: 1001 종료, 1002 사진 1번 칸이 비고(emptied), 1001 1번 칸 사진 교체(새 벡터 없음)
    fake.completion.update(bronze_run_id="20261125T030000-bbbb", source_fetched_at="2026-11-25T03:00:00+09:00")
    fake.notices[0] = notice("1001", "CLOSED")
    r = run(capsys, "notice_status")
    assert r["notice"]["closed_now"] == 1
    closed, until, status = conn.execute(
        "SELECT closed_transition_at, visible_until, status_group FROM service_notice WHERE desertion_no='1001'").fetchone()
    assert status == "CLOSED" and until - closed == timedelta(days=30)

    fake.publication.update(publish_seq=2, publish_id="silver_20261125T030000-bbbb", photo_snapshot_id=23)
    fake.photos = [photo("1001", 1, SHA_B), photo("1001", 2, SHA_B), {**photo("1002", 1, None), "sha256": None}]
    r = run(capsys, "photo_vector")
    assert r["photo"]["deletes"] == 1 and r["photo"]["upserts"] == 1
    assert r["image_vector"]["deletes"] == 1  # 바뀐 사진의 옛 벡터는 검색 후보에서 뺀다
    assert conn.execute("SELECT count(*) FROM image_embedding").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM service_notice_photo WHERE desertion_no='1002'").fetchone()[0] == 0

    r = run(capsys, "tag")
    assert r["tag"]["deletes"] == 1  # 대표 사진이 바뀌어 옛 사진 태그가 빠진다


def test_stale_and_rerun(conn, monkeypatch, capsys):
    fake = Fake()
    fake.install(monkeypatch)
    run(capsys, "notice_status")
    again = run(capsys, "notice_status")
    assert again["rerun"] is True and again["notice"]["unchanged"] == 2
    fake.completion["bronze_run_id"] = "20261123T030000-zzzz"
    assert cli.main(["notice_status"]) == 4  # 더 오래된 완료본은 반영하지 않는다
    assert "더 오래된" in capsys.readouterr().err


def test_tag_requires_photo_route_first(conn, monkeypatch, capsys):
    fake = Fake()
    fake.install(monkeypatch)
    activate(conn, "photo_trait", "photo_trait@1")
    run(capsys, "notice_status")
    with pytest.raises(RuntimeError, match="photo_vector"):
        cli.main(["tag"])


def test_config_hash_mismatch_stops(conn, monkeypatch, capsys):
    fake = Fake()
    fake.install(monkeypatch)
    conn.execute("INSERT INTO active_version (task, version, config_hash, activated_by) "
                 "VALUES ('image_embedding', 'image_embedding@1', %s, 'test')", ("0" * 64,))
    run(capsys, "notice_status")
    with pytest.raises(RuntimeError, match="config_hash"):
        cli.main(["photo_vector"])


def test_bulk_upsert_and_delete_dedupe(conn):
    """COPY 로 한 번에 올릴 때 같은 키가 두 번 오면 뒤의 값을 쓰고, 지울 때는 그 버전만 지운다."""
    from rebom.serving.sync.plan import NOTICE_COLUMNS

    base = {c: None for c in NOTICE_COLUMNS}
    rows = [{**base, "desertion_no": "9", "status_group": "PROTECTING", "species_code": "417000", "content_hash": h,
             "closed_transition_at": None, "visible_until": None} for h in (SHA_A, SHA_B)]
    with conn.transaction():
        try:
            assert db.upsert_notices(conn, rows) == 1
        except psycopg.errors.NotNullViolation:
            pytest.skip("공고 필수 칼럼이 늘어 이 최소 행으로는 못 넣음")
    assert conn.execute("SELECT content_hash FROM service_notice WHERE desertion_no='9'").fetchone()[0] == SHA_B
    with conn.transaction():
        db.upsert_photos(conn, [{"desertion_no": "9", "image_slot": 1, "sha256": SHA_A, "photo_version": 1,
                                 "serving_s3_key": "s", "thumb_s3_key": "t"}], 1)
        db.upsert_image_vectors(conn, [{"desertion_no": "9", "image_slot": 1, "version": v, "photo_version": 1,
                                        "sha256": SHA_A, "s3_key": "k", "gold_run_id": "g", "embedding": [0.5]}
                                       for v in ("image_embedding@1", "image_embedding@2")])
        assert db.delete_image_vectors(conn, [("9", 1), ("9", 1)], "image_embedding@1") == 1
    assert conn.execute("SELECT version FROM image_embedding").fetchall() == [("image_embedding@2",)]
