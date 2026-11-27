"""운영 기록 쓰기(ops_log.write)와 화면 질의(OpsStore)를 실제 PostgreSQL 에 돌려 본다.

REBOM_TEST_PG_DSN 이 있을 때만 돈다. 예: REBOM_TEST_PG_DSN="host=/tmp port=55432 user=postgres dbname=opstest"
⚠ 그 DB 의 public 스키마를 지우고 다시 만든다. 운영 DB 를 넣지 말 것.
"""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from rebom.common import ops_log, ops_summary  # noqa: E402
from rebom.serving import ops_api  # noqa: E402
from rebom.serving.ops_store import OpsNotReady, OpsStore  # noqa: E402

DSN = os.environ.get("REBOM_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="REBOM_TEST_PG_DSN 없음")
MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
RUN = "20261127T030011-9c41a2"
AT = "2026-11-27T03:44:02+09:00"


def _reset(with_ops: bool = True):
    c = psycopg.connect(DSN, autocommit=True)
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    names = ["003_service_tables.sql", "005_schema_migrations.sql"]
    if with_ops:
        names += ["008_ops_records.sql", "009_ops_reason_codes.sql"]
    for name in names:
        c.execute((MIGRATIONS / name).read_text(encoding="utf-8"))
    return c


@pytest.fixture()
def conn():
    c = _reset()
    yield c
    c.close()


def _store():
    return OpsStore(lambda: psycopg.connect(DSN, autocommit=True))


def test_009_accepts_new_codes_and_still_rejects_unknown(conn):
    row = ops_log.failure_row("test", RUN, "bronze", "partition", "k", "api_timeout", observed_at=AT)
    ops_log.write(conn, failures=[row])
    bad = {**row, "reason_code": "made_up"}
    with pytest.raises(psycopg.errors.CheckViolation):
        ops_log.write(conn, failures=[bad])


def test_write_then_read_overview_failures_and_schema(conn):
    manifest = {"run_id": RUN, "fetched_at": "2026-11-27T03:00:11+09:00", "status": "ok", "dropped_count": 1,
                "files": [{"kind": "updated", "record_count": 10}],
                "partitions": [
                    {"kind": "list_protect", "start_date": "2024-05-09", "end_date": "2024-05-09", "total_count": 31,
                     "received_count": 30, "complete": False, "reason": "count_mismatch"},
                    {"kind": "updated", "start_date": "2026-11-26", "end_date": "2026-11-27", "total_count": 10,
                     "received_count": 10, "complete": True, "api_errors": {"timeout": 2}}]}
    stage, failures = ops_summary.bronze_rows("test", manifest, state={"last_run_id": RUN})
    photo = ops_log.stage_row("test", RUN, "photo", "warn", started_at=AT, failed_count=1)
    photo_fail = ops_log.failure_row("test", RUN, "photo", "photo_slot", "448567202600142#2", "not_found",
                                     observed_at=AT)
    schema = ops_log.schema_row("test", "silver", "silver_animal_notice", "rebom_test.notice", 3,
                                [("desertion_no", "string")], observed_at=AT, bronze_run_id=RUN)
    out = ops_log.write(conn, stages=[stage, photo], failures=[*failures, photo_fail], schemas=[schema])
    assert out == {"stages": 2, "failures": 4, "schemas": 1}
    # 같은 실행을 다시 쓰면 두 벌이 되지 않는다
    ops_log.write(conn, stages=[stage, photo], failures=[*failures, photo_fail], schemas=[schema])
    assert conn.execute("SELECT count(*) FROM ops_failure").fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM ops_schema_snapshot").fetchone()[0] == 1
    conn.execute("INSERT INTO service_notice (desertion_no, status_group, species_code, content_hash) "
                 "VALUES ('448567202600142', 'PROTECTING', '417000', %s)", ("c" * 64,))

    store = _store()
    latest = store.latest_bronze("test")
    view = ops_api.overview(latest, store.last_success_bronze("test"), store.run_stages("test", RUN),
                            store.failure_counts("test", RUN), "2026-11-27")
    assert view["collection"]["status"] == "ok" and view["collection"]["lastSuccessRunId"] == RUN
    assert [s["stage"] for s in view["stages"]] == ["bronze", "photo"]
    reasons = {r["reasonCode"]: r for r in view["reasons"]}
    assert reasons["count_mismatch"]["knownException"] == 1 and reasons["count_mismatch"]["actionable"] == 0
    assert reasons["api_timeout"]["recovered"] == 1
    assert view["cards"]["collectFailures"] == 1  # 사진 404 한 건만 실제 문제

    total, rows = store.failures("test", RUN, "bronze", None, False, 10, 0)
    assert total == 3 and {r["reason_code"] for r in rows} == {"count_mismatch", "api_timeout", "dropped_row"}
    assert store.failures("test", RUN, None, None, True, 10, 0)[0] == 0
    trace = store.animal_failures("test", "448567202600142")
    assert [r["subject_key"] for r in trace] == ["448567202600142#2"]
    assert store.animal_service_row("448567202600142")["photos"] == 0
    assert len(store.runs_since("test", "20261120")) == 2

    sv = ops_api.schema_view(ops_api.load_catalog(), store.schema_snapshots("test"), store.rds_columns(),
                             store.applied_migrations())
    by = {t["table"]: t for t in sv["tables"]}
    assert by["ops_failure"]["matches"] is True  # 실제 칼럼 = DBML
    assert by["service_notice"]["matches"] is True
    assert by["silver_animal_notice"]["observed"] and by["silver_animal_notice"]["versions"] == 1
    assert "009_ops_reason_codes.sql" in sv["pendingMigrations"]  # 적용 기록(schema_migrations)이 비어 있음


def test_store_reports_missing_tables():
    c = _reset(with_ops=False)
    c.close()
    with pytest.raises(OpsNotReady):
        _store().latest_bronze("test")
