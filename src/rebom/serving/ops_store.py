"""운영 화면(/ops)이 읽는 RDS 질의. 쓰기는 없다(기록은 rebom.common.ops_log 가 쓴다).

표: ops_run_summary · ops_failure · ops_schema_snapshot (마이그레이션 008·009), service_notice, schema_migrations,
information_schema.columns. 표가 아직 없으면(008 적용 전) OpsNotReady 를 낸다.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Sequence

SUMMARY_COLS = ("bronze_run_id, publish_id, gold_run_id, stage, status, started_at, finished_at, input_count, "
                "output_count, failed_count, stats, code_version, created_at")
FAILURE_COLS = ("id, bronze_run_id, publish_id, stage, subject_type, subject_key, reason_code, message, attempts, "
                "evidence, evidence_ref, blocking, observed_at")


class OpsNotReady(RuntimeError):
    """운영 기록 표가 없다(마이그레이션 008 적용 전)."""


class OpsStore:
    def __init__(self, connect: Callable[[], Any]):
        self._connect = connect
        self._conn = None
        self._lock = threading.Lock()

    def rows(self, sql: str, args: Sequence[Any] = ()) -> list[dict[str, Any]]:
        import psycopg
        from psycopg.rows import dict_row

        with self._lock:
            for attempt in range(2):
                if self._conn is None or self._conn.closed:
                    self._conn = self._connect()
                try:
                    with self._conn.cursor(row_factory=dict_row) as cur:
                        cur.execute(sql, args)
                        return list(cur.fetchall())
                except psycopg.errors.UndefinedTable as exc:
                    raise OpsNotReady(str(exc).splitlines()[0]) from None
                except psycopg.OperationalError:
                    self._conn = None
                    if attempt:
                        raise
        return []

    # ───────── 실행·단계 ─────────

    def latest_bronze(self, env: str) -> dict[str, Any] | None:
        rows = self.rows(f"SELECT {SUMMARY_COLS} FROM ops_run_summary WHERE env = %s AND stage = 'bronze' "
                         "ORDER BY bronze_run_id DESC LIMIT 1", (env,))
        return rows[0] if rows else None

    def last_success_bronze(self, env: str) -> dict[str, Any] | None:
        rows = self.rows(f"SELECT {SUMMARY_COLS} FROM ops_run_summary WHERE env = %s AND stage = 'bronze' "
                         "AND status IN ('ok', 'warn') ORDER BY bronze_run_id DESC LIMIT 1", (env,))
        return rows[0] if rows else None

    def run_stages(self, env: str, run_id: str) -> list[dict[str, Any]]:
        return self.rows(f"SELECT {SUMMARY_COLS} FROM ops_run_summary WHERE env = %s AND bronze_run_id = %s "
                         "ORDER BY started_at, stage", (env, run_id))

    def runs_since(self, env: str, first_run_prefix: str) -> list[dict[str, Any]]:
        """실행 id 가 first_run_prefix(YYYYMMDD) 이상인 실행들의 단계 요약 (14일 추이)."""
        return self.rows("SELECT bronze_run_id, stage, status, input_count, output_count, failed_count, started_at "
                         "FROM ops_run_summary WHERE env = %s AND bronze_run_id >= %s "
                         "ORDER BY bronze_run_id, stage", (env, first_run_prefix))

    # ───────── 실패 ─────────

    def failure_counts(self, env: str, run_id: str) -> list[dict[str, Any]]:
        return self.rows(
            "SELECT stage, reason_code, count(*) AS count, "
            "count(*) FILTER (WHERE blocking) AS blocking, "
            "count(*) FILTER (WHERE evidence->>'known_exception' = 'true') AS known_exception, "
            "count(*) FILTER (WHERE evidence->>'recovered' = 'true') AS recovered "
            "FROM ops_failure WHERE env = %s AND bronze_run_id = %s "
            "GROUP BY stage, reason_code ORDER BY stage, count(*) DESC", (env, run_id))

    def failures(self, env: str, run_id: str, stage: str | None, reason_code: str | None, blocking_only: bool,
                 limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
        where, args = ["env = %s", "bronze_run_id = %s"], [env, run_id]
        if stage:
            where.append("stage = %s")
            args.append(stage)
        if reason_code:
            where.append("reason_code = %s")
            args.append(reason_code)
        if blocking_only:
            where.append("blocking")
        cond = " AND ".join(where)
        total = self.rows(f"SELECT count(*) AS n FROM ops_failure WHERE {cond}", args)[0]["n"]
        rows = self.rows(f"SELECT {FAILURE_COLS} FROM ops_failure WHERE {cond} "
                         "ORDER BY blocking DESC, stage, reason_code, subject_key LIMIT %s OFFSET %s",
                         [*args, limit, offset])
        return int(total), rows

    def animal_failures(self, env: str, desertion_no: str, limit: int = 200) -> list[dict[str, Any]]:
        """공고번호 하나의 실패 기록 (공고 대상 = 번호, 사진 칸 = 번호#칸)."""
        return self.rows(f"SELECT {FAILURE_COLS} FROM ops_failure WHERE env = %s "
                         "AND (subject_key = %s OR subject_key LIKE %s) ORDER BY observed_at DESC, id DESC LIMIT %s",
                         (env, desertion_no, f"{desertion_no}#%", limit))

    def animal_service_row(self, desertion_no: str) -> dict[str, Any] | None:
        rows = self.rows("SELECT n.desertion_no, n.status_group, n.process_state_raw, n.closed_transition_at, "
                         "n.visible_until, n.silver_publish_seq, n.synced_at, "
                         "(SELECT count(*) FROM service_notice_photo p WHERE p.desertion_no = n.desertion_no) AS photos, "
                         "(SELECT count(*) FROM service_notice_tag t WHERE t.desertion_no = n.desertion_no) AS tags "
                         "FROM service_notice n WHERE n.desertion_no = %s", (desertion_no,))
        return rows[0] if rows else None

    # ───────── 스키마 ─────────

    def schema_snapshots(self, env: str) -> list[dict[str, Any]]:
        """표마다 가장 최근 관측과 지금까지 바뀐 횟수."""
        return self.rows(
            "SELECT DISTINCT ON (table_name) table_name, layer, physical_name, schema_id, fingerprint, columns, "
            "observed_at, bronze_run_id, count(*) OVER (PARTITION BY table_name) AS versions "
            "FROM ops_schema_snapshot WHERE env = %s ORDER BY table_name, observed_at DESC, id DESC", (env,))

    def rds_columns(self) -> list[dict[str, Any]]:
        return self.rows("SELECT table_name, column_name, data_type FROM information_schema.columns "
                         "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position")

    def applied_migrations(self) -> list[dict[str, Any]]:
        try:
            return self.rows("SELECT filename, sha256, applied_at, applied_by, how FROM schema_migrations "
                             "ORDER BY filename")
        except OpsNotReady:
            return []
