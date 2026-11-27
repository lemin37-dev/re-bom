"""운영 화면 API(/ops) 시험: 비밀번호 로그인, 화면용 모양(수집 상태·원인별·스키마 비교), 스키마 카탈로그 최신 여부.

RDS 없이 돈다. 질의는 가짜 저장소(FakeStore)가 돌려주는 행으로 대신한다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from rebom.common.config import DataMode, Settings  # noqa: E402
from rebom.serving import ops_api, ops_auth  # noqa: E402
from rebom.serving.app import create_app  # noqa: E402
from rebom.serving.ops_store import OpsNotReady  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "correct horse battery"
SECRET = "test-signing-secret"
RUN = "20261127T030011-9c41a2"
PREV = "20261126T030009-5b1c2d"


# ───────── 비밀번호·쿠키 ─────────

def test_password_hash_round_trip_and_wrong_formats():
    stored = ops_auth.hash_password(PASSWORD, iterations=1000)
    assert stored.startswith("pbkdf2_sha256$1000$")
    assert ops_auth.verify_password(PASSWORD, stored)
    assert not ops_auth.verify_password("wrong", stored)
    for bad in ("", "plain", "md5$1$a$b", "pbkdf2_sha256$x$y$z"):
        assert not ops_auth.verify_password(PASSWORD, bad)
    with pytest.raises(ValueError):
        ops_auth.hash_password("")


def test_token_expires_and_rejects_tampering():
    token = ops_auth.make_token(SECRET, hours=1, now=1000)
    assert ops_auth.token_valid(SECRET, token, now=1000 + 3599)
    assert not ops_auth.token_valid(SECRET, token, now=1000 + 3601)
    assert not ops_auth.token_valid("other-secret", token, now=1001)
    expires, sig = token.split(".")
    assert not ops_auth.token_valid(SECRET, f"{int(expires) + 99999}.{sig}", now=1001)
    for bad in (None, "", "abc", "1.2.3"):
        assert not ops_auth.token_valid(SECRET, bad, now=1001)


def test_limiter_blocks_after_failures_and_forgets_after_window():
    lim = ops_auth.LoginLimiter(max_failures=3, window=60)
    for t in (0, 1, 2):
        lim.failed("a", now=t)
    assert lim.blocked("a", now=3) and not lim.blocked("b", now=3)
    assert not lim.blocked("a", now=70)
    lim.failed("a", now=71)
    lim.succeeded("a")
    assert not lim.blocked("a", now=72)


# ───────── 화면용 모양 ─────────

BRONZE_OK = {"bronze_run_id": RUN, "stage": "bronze", "status": "warn", "output_count": 8697,
             "stats": {"run_status": "ok", "last_success_run_id": RUN, "last_success_at": "2026-11-27T03:00:11+09:00"}}


def test_collection_status_ok_aborted_not_run():
    ok = ops_api.collection_status(BRONZE_OK, None, "2026-11-27")
    assert (ok["status"], ok["warn"], ok["lastSuccessRunId"]) == ("ok", True, RUN)
    aborted_row = {"bronze_run_id": RUN, "stage": "bronze", "status": "failed",
                   "stats": {"run_status": "aborted", "last_success_run_id": PREV}}
    aborted = ops_api.collection_status(aborted_row, None, "2026-11-27")
    assert (aborted["status"], aborted["lastSuccessRunId"], aborted["lastSuccessAt"]) == (
        "aborted", PREV, "2026-11-26T03:00:09+09:00")
    # 오늘 bronze 줄이 없으면 실행 안 됨. 마지막 성공은 상태 파일 값이 없으면 ok·warn 인 가장 최근 줄
    not_run = ops_api.collection_status({"bronze_run_id": PREV, "status": "ok", "stats": "{}"},
                                        {"bronze_run_id": PREV}, "2026-11-27")
    assert (not_run["status"], not_run["lastRunId"], not_run["lastSuccessRunId"]) == ("not_run", PREV, PREV)
    assert ops_api.collection_status(None, None, "2026-11-27")["status"] == "not_run"


def test_overview_cards_separate_known_and_recovered_from_actionable():
    stages = [BRONZE_OK,
              {"bronze_run_id": RUN, "stage": "gold_photo_trait", "status": "warn", "failed_count": 3, "stats": {}},
              {"bronze_run_id": RUN, "stage": "silver_notice", "status": "failed",
               "stats": {"insert_count": 286, "update_count": 512, "transitions": {"보호중 → 종료(입양)": 41}}},
              {"bronze_run_id": RUN, "stage": "photo", "status": "ok", "stats": {}}]
    counts = [
        {"stage": "bronze", "reason_code": "count_mismatch", "count": 3, "blocking": 0, "known_exception": 2, "recovered": 0},
        {"stage": "bronze", "reason_code": "api_timeout", "count": 2, "blocking": 0, "known_exception": 0, "recovered": 2},
        {"stage": "photo", "reason_code": "not_found", "count": 4, "blocking": 0, "known_exception": 0, "recovered": 0},
        {"stage": "silver_notice", "reason_code": "raw_content_conflict", "count": 1, "blocking": 1,
         "known_exception": 0, "recovered": 0},
        {"stage": "rds_tag", "reason_code": "stale_input", "count": 1, "blocking": 0, "known_exception": 0, "recovered": 0},
    ]
    view = ops_api.overview(BRONZE_OK, None, stages, counts, "2026-11-27")
    assert [s["stage"] for s in view["stages"]] == ["bronze", "photo", "silver_notice", "gold_photo_trait"]
    assert view["date"] == "2026-11-27" and view["collection"]["status"] == "ok"
    cards = view["cards"]
    assert (cards["received"], cards["inserted"], cards["updated"]) == (8697, 286, 512)
    assert cards["collectFailures"] == 1 + 4  # 알려진 예외 2건·재시도로 받아낸 2건은 빼고
    assert (cards["cleanBlocking"], cards["modelFailures"], cards["syncFailures"]) == (1, 3, 1)
    timeout = next(r for r in view["reasons"] if r["reasonCode"] == "api_timeout")
    assert (timeout["recovered"], timeout["actionable"]) == (2, 0)


def test_failure_view_reads_flags_from_evidence():
    row = {"id": 7, "bronze_run_id": RUN, "stage": "bronze", "subject_type": "partition",
           "subject_key": "list_protect 2024-05-09~2024-05-09", "reason_code": "count_mismatch", "blocking": False,
           "evidence": '{"known_exception": true, "known_reason": "알려진 예외(2024-05-09)"}', "message": "m"}
    view = ops_api.failure_view(row)
    assert view["knownException"] and not view["recovered"] and view["evidence"]["known_reason"].startswith("알려진")


def test_schema_view_compares_names_and_migrations():
    catalog = {"rds_version": "v0.3", "tables": {
        "silver_animal_notice": {"layer": "silver", "source": "s", "columns": [["desertion_no", "string"], ["a", "int"]],
                                 "fingerprint": "f1"},
        "gold_run_input": {"layer": "gold", "source": "g", "columns": [["gold_run_id", "string"]], "fingerprint": "f2"},
        "service_notice": {"layer": "rds", "source": "r", "columns": [["desertion_no", "varchar"]], "fingerprint": "f3"},
        "rescue_state": {"layer": "bronze", "source": "b", "columns": [["last_run_id", "string"]], "fingerprint": "f4"},
    }, "migrations": [{"filename": "001_init.sql", "sha256": "aa"}, {"filename": "009_x.sql", "sha256": "bb"}]}
    snaps = [{"table_name": "silver_animal_notice", "physical_name": "rebom_test.notice", "schema_id": 3,
              "fingerprint": "zz ", "columns": [["desertion_no", "string"], ["b", "int"]], "versions": 2}]
    rds = [{"table_name": "service_notice", "column_name": "desertion_no"}]
    view = ops_api.schema_view(catalog, snaps, rds, [{"filename": "001_init.sql", "sha256": "aa"}])
    by = {t["table"]: t for t in view["tables"]}
    assert (by["silver_animal_notice"]["missingInReal"], by["silver_animal_notice"]["extraInReal"]) == (["a"], ["b"])
    assert by["silver_animal_notice"]["fingerprint"] == "zz" and not by["silver_animal_notice"]["matches"]
    assert by["gold_run_input"]["observed"] is False
    assert by["service_notice"]["matches"] is True
    assert by["rescue_state"]["observed"] is None
    assert view["pendingMigrations"] == ["009_x.sql"]
    assert view["migrations"][0]["shaMatches"] is True


def test_catalog_is_up_to_date_with_dbml_and_migrations():
    spec = importlib.util.spec_from_file_location("build_schema_catalog",
                                                  ROOT / "scripts" / "schema" / "build_schema_catalog.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_schema_catalog"] = mod
    spec.loader.exec_module(mod)
    assert mod.main(["--check"]) == 0, "python scripts/schema/build_schema_catalog.py 로 다시 만드세요"
    catalog = ops_api.load_catalog()
    assert {"silver_animal_notice", "gold_run_input", "ops_failure", "service_notice"} <= set(catalog["tables"])
    assert catalog["migrations"][-1]["filename"].startswith("009_")


# ───────── 라우터 ─────────

class FakeStore:
    def __init__(self, ready=True):
        self.ready = ready
        self.calls = []

    def _check(self):
        if not self.ready:
            raise OpsNotReady("relation \"ops_run_summary\" does not exist")

    def latest_bronze(self, env):
        self._check()
        self.calls.append(("latest", env))
        return BRONZE_OK

    def last_success_bronze(self, env):
        return BRONZE_OK

    def run_stages(self, env, run_id):
        return [BRONZE_OK]

    def failure_counts(self, env, run_id):
        return [{"stage": "bronze", "reason_code": "api_http_error", "count": 1, "blocking": 0,
                 "known_exception": 0, "recovered": 0}]

    def runs_since(self, env, first):
        self.calls.append(("runs", first))
        return [{"bronze_run_id": RUN, "stage": "bronze", "status": "ok", "input_count": 1, "output_count": 1,
                 "failed_count": 0}]

    def failures(self, env, run_id, stage, reason, blocking, limit, offset):
        self.calls.append(("failures", run_id, stage, reason, blocking, limit, offset))
        return 1, [{"id": 1, "bronze_run_id": run_id, "stage": "bronze", "subject_type": "partition",
                    "subject_key": "updated 2026-11-26~2026-11-27", "reason_code": "api_http_error",
                    "evidence": {"recovered": True}, "blocking": False}]

    def animal_failures(self, env, no):
        return [{"id": 2, "bronze_run_id": RUN, "stage": "photo", "subject_type": "photo_slot",
                 "subject_key": f"{no}#2", "reason_code": "not_found", "evidence": {}, "blocking": False}]

    def animal_service_row(self, no):
        return {"desertion_no": no, "status_group": "PROTECTING", "photos": 3, "tags": 5}

    def schema_snapshots(self, env):
        return []

    def rds_columns(self):
        return []

    def applied_migrations(self):
        return []


def _client(store=None, **over) -> TestClient:
    settings = Settings(_env_file=None, data_mode=DataMode.DB, db_dsn="postgresql://unused",
                        ops_password_hash=ops_auth.hash_password(PASSWORD, iterations=1000),
                        ops_session_secret=SECRET, ops_cookie_secure=False, **over)
    app = create_app(settings)
    app.state.ops_store = store or FakeStore()
    ops_api._global.__init__(max_failures=30)  # 다른 시험의 전역 실패 횟수를 비움
    return TestClient(app)


def _login(client: TestClient):
    return client.post("/ops/login", json={"password": PASSWORD})


def test_ops_is_503_without_password_settings_or_in_sample_mode():
    plain = TestClient(create_app(Settings(_env_file=None, data_mode=DataMode.SAMPLE)))
    assert plain.post("/ops/login", json={"password": "x"}).status_code == 503
    assert plain.get("/ops/overview").status_code == 503
    no_secret = TestClient(create_app(Settings(_env_file=None, data_mode=DataMode.DB, db_dsn="postgresql://unused",
                                               ops_password_hash="pbkdf2_sha256$1$a$b")))
    assert no_secret.get("/ops/me").status_code == 503


def test_login_sets_http_only_cookie_and_unlocks_endpoints():
    client = _client()
    assert client.get("/ops/overview").status_code == 401
    assert client.post("/ops/login", json={"password": "wrong"}).status_code == 401
    res = _login(client)
    assert res.status_code == 204
    cookie = res.headers["set-cookie"]
    assert "rebom_ops=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
    assert client.get("/ops/me").json() == {"ok": True}
    body = client.get("/ops/overview").json()
    assert body["env"] == "test" and body["runId"] == RUN and body["reasons"][0]["reasonCode"] == "api_http_error"
    assert client.post("/ops/logout").status_code == 204
    client.cookies.clear()
    assert client.get("/ops/me").status_code == 401


def test_secure_cookie_by_default():
    settings = Settings(_env_file=None, data_mode=DataMode.DB, db_dsn="postgresql://unused",
                        ops_password_hash=ops_auth.hash_password(PASSWORD, iterations=1000), ops_session_secret=SECRET)
    app = create_app(settings)
    app.state.ops_store = FakeStore()
    res = TestClient(app).post("/ops/login", json={"password": PASSWORD})
    assert "Secure" in res.headers["set-cookie"]


def test_too_many_wrong_passwords_is_429():
    client = _client()
    codes = [client.post("/ops/login", json={"password": "nope"}).status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]
    assert _login(client).status_code == 429  # 맞는 비밀번호도 잠시 막힘


def test_endpoints_pass_filters_and_validate_input():
    store = FakeStore()
    client = _client(store)
    _login(client)
    body = client.get("/ops/failures", params={"run_id": RUN, "stage": "bronze", "blocking": "true", "limit": 5}).json()
    assert body["total"] == 1 and body["items"][0]["recovered"] is True
    assert store.calls[-1] == ("failures", RUN, "bronze", None, True, 5, 0)
    assert client.get("/ops/failures", params={"run_id": "bad"}).status_code == 422
    assert client.get("/ops/failures", params={"run_id": RUN, "stage": "x;drop"}).status_code == 422
    animal = client.get("/ops/animals/448567202600142").json()
    assert animal["service"]["status_group"] == "PROTECTING" and animal["failures"][0]["subjectKey"].endswith("#2")
    assert client.get("/ops/animals/abc").status_code == 422
    runs = client.get("/ops/runs", params={"days": 3}).json()
    assert runs["runs"][0]["stages"]["bronze"]["status"] == "ok"
    schema = client.get("/ops/schema").json()
    assert schema["rdsVersion"] == "v0.3" and schema["pendingMigrations"]  # 적용 기록이 없으면 모두 남은 것


def test_missing_ops_tables_is_503_with_next_step():
    client = _client(FakeStore(ready=False))
    _login(client)
    res = client.get("/ops/overview")
    assert res.status_code == 503 and "008" in res.json()["detail"]
