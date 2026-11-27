"""운영 모니터링 화면(/ops) API. 비밀번호 로그인 뒤 RDS 운영 기록을 읽기만 한다 (docs/data_contract.md 16.4).

  POST /ops/login        {"password"} → 서명 쿠키. 틀리면 401, 너무 많이 틀리면 429
  POST /ops/logout
  GET  /ops/me           로그인 확인
  GET  /ops/overview     "오늘"(마지막 수집일) 카드·단계 띠·원인별 건수 + 수집 상태(ok / aborted / not_run)·마지막 성공
  GET  /ops/runs         최근 N일 실행별 단계 상태 (추이)
  GET  /ops/failures     한 실행의 실패 건별 목록 (단계·원인·차단만 거르기)
  GET  /ops/animals/{no} 공고 하나 추적: 실패 기록 + 서비스 표 상태
  GET  /ops/schema       문서(DBML) 칼럼 vs 실제(Silver·Gold 관측, RDS information_schema) + 마이그레이션 적용 상태

db 모드 + REBOM_OPS_PASSWORD_HASH·REBOM_OPS_SESSION_SECRET 이 있을 때만 열린다(없으면 503). 응답에 보호자 정보는 없다
(기록에 애초에 넣지 않고, ops_log.scrub 이 한 번 더 지운다).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from importlib import resources
from typing import Any, Iterable, Literal, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from rebom.common.config import DataMode
from rebom.serving import ops_auth
from rebom.serving.ops_store import OpsNotReady

log = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
RUN_ID = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})-[0-9a-f]{6}$")
STAGE_ORDER = ("bronze", "photo", "silver_notice", "silver_photo", "silver_publish")
_global = ops_auth.LoginLimiter(max_failures=30)


# ───────── 화면용 모양 (순수 함수, DB 없이 시험) ─────────

def run_date(run_id: str | None) -> str | None:
    m = RUN_ID.fullmatch(run_id or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def run_started_at(run_id: str | None) -> str | None:
    m = RUN_ID.fullmatch(run_id or "")
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}+09:00"


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (datetime, date)) else value


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def stage_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "stage": row["stage"], "status": row["status"], "runId": row.get("bronze_run_id"),
        "publishId": row.get("publish_id"), "goldRunId": row.get("gold_run_id"),
        "startedAt": _iso(row.get("started_at")), "finishedAt": _iso(row.get("finished_at")),
        "inputCount": row.get("input_count"), "outputCount": row.get("output_count"),
        "failedCount": row.get("failed_count"), "stats": _json(row.get("stats")) or {},
        "codeVersion": row.get("code_version"),
    }


def failure_view(row: Mapping[str, Any]) -> dict[str, Any]:
    evidence = _json(row.get("evidence")) or {}
    return {
        "id": row.get("id"), "runId": row.get("bronze_run_id"), "publishId": row.get("publish_id"),
        "stage": row["stage"], "subjectType": row["subject_type"], "subjectKey": row["subject_key"],
        "reasonCode": row["reason_code"], "message": row.get("message"), "attempts": row.get("attempts"),
        "blocking": bool(row.get("blocking")), "knownException": bool(evidence.get("known_exception")),
        "recovered": bool(evidence.get("recovered")), "evidence": evidence, "evidenceRef": row.get("evidence_ref"),
        "observedAt": _iso(row.get("observed_at")),
    }


def collection_status(latest: Mapping[str, Any] | None, last_success: Mapping[str, Any] | None,
                      today: str) -> dict[str, Any]:
    """오늘(KST) 수집 상태: ok / aborted / not_run 과 마지막 성공.

    출처: bronze 단계 줄(수집 manifest status 를 stats.run_status 로 옮긴 것), 마지막 성공은 수집 상태 파일의
    last_run_id(stats.last_success_run_id). 그 값이 없으면 상태가 ok·warn 인 가장 최근 bronze 줄.
    """
    stats = _json((latest or {}).get("stats")) or {}
    latest_id = (latest or {}).get("bronze_run_id")
    if not latest or run_date(latest_id) != today:
        status = "not_run"
    elif stats.get("run_status") == "aborted" or latest.get("status") == "failed":
        status = "aborted"
    else:
        status = "ok"
    success_id = stats.get("last_success_run_id") or (last_success or {}).get("bronze_run_id")
    return {
        "status": status, "today": today,
        "lastRunId": latest_id, "lastRunAt": run_started_at(latest_id), "lastRunStatus": (latest or {}).get("status"),
        "warn": bool(latest and status == "ok" and latest.get("status") == "warn"),
        "lastSuccessRunId": success_id,
        "lastSuccessAt": stats.get("last_success_at") or run_started_at(success_id),
    }


def reason_counts(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """원인별 건수. 알려진 예외·재시도로 받아낸 건은 따로 세어 화면이 '실제 문제'와 나눠 보여 준다."""
    out = []
    for r in rows:
        count, known, recovered = int(r["count"]), int(r.get("known_exception") or 0), int(r.get("recovered") or 0)
        out.append({"stage": r["stage"], "reasonCode": r["reason_code"], "count": count,
                    "blocking": int(r.get("blocking") or 0), "knownException": known, "recovered": recovered,
                    "actionable": count - known - recovered})
    return out


def _stage_rank(stage: str) -> tuple[int, str]:
    if stage in STAGE_ORDER:
        return STAGE_ORDER.index(stage), stage
    return (len(STAGE_ORDER) + (0 if stage.startswith("gold_") else 1)), stage


def overview(latest: Mapping[str, Any] | None, last_success: Mapping[str, Any] | None,
             stages: Iterable[Mapping[str, Any]], counts: Iterable[Mapping[str, Any]], today: str) -> dict[str, Any]:
    stage_list = sorted((stage_view(s) for s in stages), key=lambda s: _stage_rank(s["stage"]))
    by_stage = {s["stage"]: s for s in stage_list}
    reasons = reason_counts(counts)

    def failed(prefix: str, blocking_only: bool = False) -> int:
        return sum(r["blocking"] if blocking_only else r["actionable"] for r in reasons
                   if r["stage"] == prefix or r["stage"].startswith(prefix + "_") or
                   (prefix == "gold" and r["stage"].startswith("gold_")))

    silver = by_stage.get("silver_notice", {}).get("stats", {})
    bronze = by_stage.get("bronze", {})
    return {
        "collection": collection_status(latest, last_success, today),
        "date": run_date((latest or {}).get("bronze_run_id")),  # "오늘" = 마지막 수집일
        "runId": (latest or {}).get("bronze_run_id"),
        "cards": {
            "received": bronze.get("outputCount"),
            "inserted": silver.get("insert_count"), "updated": silver.get("update_count"),
            "transitions": silver.get("transitions") or {},
            "collectFailures": failed("bronze") + failed("photo"),
            "cleanBlocking": failed("silver", blocking_only=True),  # 정제 카드는 실제 차단 건만 (lemin 11/27)
            "modelFailures": sum(s.get("failedCount") or 0 for s in stage_list if s["stage"].startswith("gold_")),
            "syncFailures": failed("rds"),
        },
        "stages": stage_list,
        "reasons": reasons,
    }


def runs_view(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    runs: dict[str, dict[str, Any]] = {}
    for r in rows:
        run = runs.setdefault(r["bronze_run_id"], {"runId": r["bronze_run_id"], "date": run_date(r["bronze_run_id"]),
                                                    "stages": {}})
        run["stages"][r["stage"]] = {"status": r["status"], "inputCount": r.get("input_count"),
                                     "outputCount": r.get("output_count"), "failedCount": r.get("failed_count")}
    return list(runs.values())


def load_catalog() -> dict[str, Any]:
    return json.loads(resources.files("rebom.serving").joinpath("ops_schema_catalog.json").read_text("utf-8"))


def _diff(doc: list[str], real: list[str]) -> dict[str, list[str]]:
    return {"missingInReal": [c for c in doc if c not in real], "extraInReal": [c for c in real if c not in doc]}


def schema_view(catalog: Mapping[str, Any], snapshots: Iterable[Mapping[str, Any]],
                rds_columns: Iterable[Mapping[str, Any]], applied: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """문서(DBML) 칼럼 이름과 실제 칼럼 이름 비교. 타입 표기는 층마다 달라(DBML vs Iceberg vs PostgreSQL) 이름만 맞춘다.
    실제 지문(fingerprint)·schema_id 와 바뀐 횟수(versions)는 관측값 그대로 보여 준다."""
    snaps = {s["table_name"]: s for s in snapshots}
    rds: dict[str, list[str]] = {}
    for c in rds_columns:
        rds.setdefault(c["table_name"], []).append(c["column_name"])
    tables = []
    for name, t in sorted(catalog["tables"].items(), key=lambda kv: (kv[1]["layer"], kv[0])):
        doc_cols = [c[0] for c in t["columns"]]
        row: dict[str, Any] = {"table": name, "layer": t["layer"], "source": t["source"], "docColumns": len(doc_cols),
                               "docFingerprint": t["fingerprint"]}
        if t["layer"] in ("silver", "gold"):
            s = snaps.get(name)
            if s:
                real = [c[0] for c in (_json(s.get("columns")) or [])]
                row.update({"observed": True, "physicalName": s.get("physical_name"), "schemaId": s.get("schema_id"),
                            "fingerprint": (s.get("fingerprint") or "").strip(), "versions": s.get("versions"),
                            "observedAt": _iso(s.get("observed_at")), **_diff(doc_cols, real)})
            else:
                row["observed"] = False
        elif t["layer"] == "rds":
            real = rds.get(name)
            row.update({"observed": real is not None, **(_diff(doc_cols, real) if real is not None else {})})
        else:
            row["observed"] = None  # Bronze 는 파일이라 관측하지 않음
        row["matches"] = bool(row.get("observed")) and not row.get("missingInReal") and not row.get("extraInReal")
        tables.append(row)
    done = {m["filename"]: m for m in applied}
    migrations = []
    for m in catalog["migrations"]:
        a = done.get(m["filename"])
        migrations.append({"filename": m["filename"], "applied": a is not None,
                           "shaMatches": None if a is None else (a.get("sha256") or "").strip() == m["sha256"],
                           "appliedAt": _iso((a or {}).get("applied_at")), "appliedBy": (a or {}).get("applied_by"),
                           "how": (a or {}).get("how")})
    return {"rdsVersion": catalog.get("rds_version"), "tables": tables, "migrations": migrations,
            "pendingMigrations": [m["filename"] for m in migrations if not m["applied"]]}


# ───────── 라우터 ─────────

class Login(BaseModel):
    password: str = Field(min_length=1, max_length=200)


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return (forwarded.split(",")[0].strip() or (request.client.host if request.client else "?"))[:64]


def _today() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def build_router(today=_today) -> APIRouter:
    router = APIRouter(prefix="/ops", tags=["ops"])

    def settings_of(request: Request):
        settings = request.app.state.settings
        if settings.data_mode != DataMode.DB or not settings.ops_enabled:
            raise HTTPException(status_code=503, detail="운영 화면이 설정되지 않았습니다(db 모드·REBOM_OPS_PASSWORD_HASH·"
                                                        "REBOM_OPS_SESSION_SECRET 필요)")
        return settings

    def require(request: Request):
        settings = settings_of(request)
        if not ops_auth.token_valid(settings.ops_session_secret, request.cookies.get(ops_auth.COOKIE)):
            raise HTTPException(status_code=401, detail="로그인이 필요합니다")
        return request.app.state.ops_store

    def run(fn, *args):
        try:
            return fn(*args)
        except OpsNotReady:
            raise HTTPException(status_code=503, detail="운영 기록 표가 없습니다. 마이그레이션 008·009 적용이 필요합니다") from None

    @router.post("/login", status_code=204)
    def login(body: Login, request: Request, response: Response) -> Response:
        settings = settings_of(request)
        key = _client_key(request)
        limiter: ops_auth.LoginLimiter = request.app.state.ops_limiter
        if limiter.blocked(key) or _global.blocked("all"):
            raise HTTPException(status_code=429, detail="잠시 뒤 다시 시도해 주세요")
        if not ops_auth.verify_password(body.password, settings.ops_password_hash):
            limiter.failed(key)
            _global.failed("all")
            log.warning("ops 로그인 실패 (%s)", key)
            raise HTTPException(status_code=401, detail="비밀번호가 다릅니다")
        limiter.succeeded(key)
        response.set_cookie(ops_auth.COOKIE, ops_auth.make_token(settings.ops_session_secret, settings.ops_session_hours),
                            max_age=int(settings.ops_session_hours * 3600), httponly=True,
                            secure=settings.ops_cookie_secure, samesite="strict", path="/")
        response.status_code = 204
        return response

    @router.post("/logout", status_code=204)
    def logout(response: Response) -> Response:
        response.delete_cookie(ops_auth.COOKIE, path="/")
        response.status_code = 204
        return response

    @router.get("/me")
    def me(_=Depends(require)) -> dict[str, bool]:
        return {"ok": True}

    def env_of(request: Request, env: str | None) -> str:
        return env or request.app.state.settings.ops_env

    @router.get("/overview")
    def get_overview(request: Request, env: Literal["test", "prod"] | None = None,
                     store=Depends(require)) -> dict[str, Any]:
        e = env_of(request, env)
        latest = run(store.latest_bronze, e)
        last_success = run(store.last_success_bronze, e)
        run_id = (latest or {}).get("bronze_run_id")
        stages = run(store.run_stages, e, run_id) if run_id else []
        counts = run(store.failure_counts, e, run_id) if run_id else []
        return {"env": e, **overview(latest, last_success, stages, counts, today())}

    @router.get("/runs")
    def get_runs(request: Request, env: Literal["test", "prod"] | None = None, days: int = Query(14, ge=1, le=90),
                 store=Depends(require)) -> dict[str, Any]:
        e = env_of(request, env)
        first = (datetime.strptime(today(), "%Y-%m-%d").date() - timedelta(days=days - 1)).strftime("%Y%m%d")
        return {"env": e, "days": days, "runs": runs_view(run(store.runs_since, e, first))}

    @router.get("/failures")
    def get_failures(request: Request, run_id: str = Query(..., pattern=RUN_ID.pattern),
                     env: Literal["test", "prod"] | None = None,
                     stage: str | None = Query(None, pattern=r"^[a-z_]{2,40}$"),
                     reason_code: str | None = Query(None, pattern=r"^[a-z_]{2,40}$"),
                     blocking: bool = False, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                     store=Depends(require)) -> dict[str, Any]:
        e = env_of(request, env)
        total, rows = run(store.failures, e, run_id, stage, reason_code, blocking, limit, offset)
        return {"env": e, "runId": run_id, "total": total, "items": [failure_view(r) for r in rows]}

    @router.get("/animals/{desertion_no}")
    def get_animal(request: Request, desertion_no: str, env: Literal["test", "prod"] | None = None,
                   store=Depends(require)) -> dict[str, Any]:
        if not re.fullmatch(r"\d{6,20}", desertion_no):
            raise HTTPException(status_code=422, detail="공고번호는 숫자입니다")
        e = env_of(request, env)
        service = run(store.animal_service_row, desertion_no)
        return {"env": e, "desertionNo": desertion_no,
                "service": {k: _iso(v) for k, v in service.items()} if service else None,
                "failures": [failure_view(r) for r in run(store.animal_failures, e, desertion_no)]}

    @router.get("/schema")
    def get_schema(request: Request, env: Literal["test", "prod"] | None = None,
                   store=Depends(require)) -> dict[str, Any]:
        e = env_of(request, env)
        return {"env": e, **schema_view(request.app.state.ops_catalog, run(store.schema_snapshots, e),
                                        run(store.rds_columns), run(store.applied_migrations))}

    return router
