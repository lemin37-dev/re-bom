"""운영 기록(마이그레이션 008, common/ops_log·ops_summary, storage/silver/ops_stats, 동기화 기록 줄) 시험. DB·S3 없이 돈다."""
from __future__ import annotations

import ast
import io
import json
import re
from pathlib import Path

import pytest

from rebom.common import ops_log, ops_summary
from rebom.storage.silver import ops_stats

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db" / "migrations" / "008_ops_records.sql"
DBML = ROOT / "docs" / "rds_schema.dbml"
RUN = "20261127T030011-9c41a2"
AT = "2026-11-27T03:44:02+09:00"


def _enum(name: str) -> list[str]:
    body = re.search(rf"^Enum {name} \{{\n(.*?)^\}}", DBML.read_text(encoding="utf-8"), re.S | re.M).group(1)
    return re.findall(r'^\s+"([^"]+)"', body, re.M)


# ───────── 값 목록이 마이그레이션·DBML·코드에서 같은지 ─────────

def test_reason_codes_match_migration_and_dbml():
    sql = MIGRATION.read_text(encoding="utf-8")
    check = re.search(r"reason_code\s+text\s+NOT NULL CHECK \(reason_code IN \((.*?)\)\)", sql, re.S).group(1)
    assert tuple(re.findall(r"'([a-z_]+)'", check)) == ops_log.REASON_CODES
    assert tuple(_enum("ops_reason_code")) == ops_log.REASON_CODES


def test_status_and_stage_match_migration_and_dbml():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert tuple(_enum("ops_run_status")) == ops_log.STATUSES
    assert f"'{ops_log.STAGE.pattern}'" in sql
    for value in _enum("ops_stage"):
        assert ops_log.STAGE.fullmatch(value.replace("<task>", "photo_trait").replace("<route>", "tag")), value
    subject = re.search(r"subject_type IN \((.*?)\)", sql).group(1)
    assert tuple(re.findall(r"'([a-z_]+)'", subject)) == ops_log.SUBJECT_TYPES


# ───────── 줄 만들기 ─────────

def test_rows_validate_and_scrub_owner_fields():
    row = ops_log.failure_row("test", RUN, "silver_notice", "animal", "448567202600142", "raw_content_conflict",
                              observed_at=AT, blocking=True,
                              evidence={"sources": ["a"], "careOwnerNm": "x", "nested": [{"callTel": "y", "ok": 1}]})
    assert row["evidence"] == {"sources": ["a"], "nested": [{"ok": 1}]}
    assert row["blocking"] is True
    with pytest.raises(ValueError):
        ops_log.failure_row("test", RUN, "silver_notice", "animal", "1", "made_up", observed_at=AT)
    with pytest.raises(ValueError):
        ops_log.stage_row("test", RUN, "silver", "ok", started_at=AT)
    with pytest.raises(ValueError):
        ops_log.stage_row("staging", RUN, "bronze", "ok", started_at=AT)
    assert ops_log.stage_row("test", RUN, "gold_photo_trait", "warn", started_at=AT)["stage"] == "gold_photo_trait"


def test_fingerprint_ignores_order_and_type_case():
    a = ops_log.fingerprint([("b", "int"), ("a", "string")])
    assert a == ops_log.fingerprint([("a", "STRING"), ("b", "int")])
    assert a != ops_log.fingerprint([("a", "string")])
    assert len(a) == 64


class FakeConn:
    def __init__(self, latest=None):
        self.sql, self.latest = [], latest

    def transaction(self):
        import contextlib

        return contextlib.nullcontext()

    def execute(self, sql, params=()):
        self.sql.append((sql.split()[0], sql, list(params)))
        conn = self

        class R:
            def fetchone(self):
                return conn.latest

        return R()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_write_replaces_failures_per_stage_and_skips_same_schema():
    st = ops_log.stage_row("test", RUN, "silver_notice", "failed", started_at=AT, stats={"x": 1})
    fl = ops_log.failure_row("test", RUN, "silver_notice", "animal", "1", "raw_content_conflict", observed_at=AT)
    sc = ops_log.schema_row("test", "gold", "gold_animal_tag", "rebom_gold.gold_animal_tag_test", 3,
                            [("subject_type", "string")], observed_at=AT)
    conn = FakeConn(latest=(sc["fingerprint"] + "  ", 3))
    out = ops_log.write(conn, stages=[st], failures=[fl], schemas=[sc],
                        replace_failures_for=[("test", RUN, "photo")])
    assert out == {"stages": 1, "failures": 1, "schemas": 0}
    kinds = [k for k, _, _ in conn.sql]
    assert kinds == ["INSERT", "DELETE", "DELETE", "INSERT", "SELECT"]
    assert "ON CONFLICT (env, bronze_run_id, stage) DO UPDATE" in conn.sql[0][1]
    assert json.loads(conn.sql[0][2][11]) == {"x": 1}  # stats 는 JSON 문자열 + ::jsonb
    conn2 = FakeConn(latest=("0" * 64, 2))
    assert ops_log.write(conn2, schemas=[sc])["schemas"] == 1


def test_safe_write_never_raises(monkeypatch, capsys):
    def boom():
        raise ConnectionError("no route to RDS")

    assert "ConnectionError" in ops_log.safe_write(stages=[], connect=boom)
    assert "OPS_LOG_SKIPPED" in capsys.readouterr().err
    monkeypatch.setenv("REBOM_OPS_LOG", "off")
    assert ops_log.safe_write(connect=boom) == "OPS_LOG_DISABLED"


# ───────── Silver 건수 기록 ─────────

def test_notice_stats_counts_without_values():
    prepared = {
        "ready": True, "run_id": RUN,
        "notice_delta_rows": [
            {"desertion_no": "1", "weight_parse_status": "ambiguous", "special_mark": "비공개 내용"},
            {"desertion_no": "2", "color_parse_status": "needs_review"},
            {"desertion_no": "3"},
        ],
        "history_rows": [
            {"event_type": "CHANGED", "previous_state_raw": "보호중", "process_state_raw": "종료(입양)"},
            {"event_type": "INITIAL", "previous_state_raw": None, "process_state_raw": "보호중"},
        ],
        "actual_closed_ids": ["2"], "inferred_closed_ids": [], "lookup_deferred_ids": [], "event_conflicts": [],
    }
    s = ops_stats.build_notice_stats(prepared, [{"desertion_no": "2"}, {"desertion_no": "3"}])
    assert (s["insert_count"], s["update_count"]) == (1, 2)
    assert s["transitions"] == {"보호중 → 종료(입양)": 1}
    assert s["parse_hold"] == {"weight_parse_status": {"ambiguous": 1}, "color_parse_status": {"needs_review": 1}}
    assert "비공개 내용" not in json.dumps(s, ensure_ascii=False)


def test_notice_stats_keeps_conflicts_and_save_failure_is_quiet(capsys):
    s = ops_stats.build_notice_stats({"ready": False, "run_id": RUN, "stage": "NOTICE_DELTA", "conflicts": [
        {"desertion_no": "448567202600142", "sources": ["list_protect", "updated"], "reason": "raw_content_conflict"}]}, [])
    assert s["conflicts"][0]["desertion_no"] == "448567202600142"

    class S3:
        def __init__(self):
            self.put = {}

        def put_object(self, Bucket, Key, Body, ContentType):
            self.put[Key] = json.loads(Body)

    s3 = S3()
    assert ops_stats.save_notice_stats_s3(s3, "b", "test", s) is None
    assert s3.put["ops/test/silver_notice/20261127T030011-9c41a2.json"]["ready"] is False

    class Broken:
        def put_object(self, **kw):
            raise PermissionError("AccessDenied")

    assert "PermissionError" in ops_stats.save_notice_stats_s3(Broken(), "b", "test", s)
    assert "OPS_STATS_SKIPPED" in capsys.readouterr().err


# ───────── 요약 태스크 ─────────

BRONZE = {
    "run_id": RUN, "fetched_at": "2026-11-27T03:00:11+09:00", "status": "ok", "mode": "incremental", "calls": 90,
    "code_version": "2026-11-03", "dropped_count": 3, "duplicate_count": 41,
    "files": [{"kind": "updated", "record_count": 1288}, {"kind": "list_protect", "record_count": 7409}],
    "partitions": [
        {"kind": "list_protect", "start_date": "20261120", "end_date": "20261127", "total_count": 7412,
         "received_count": 7409, "complete": False, "reason": "count_mismatch"},
        {"kind": "updated", "start_date": "20261126", "end_date": "20261127", "total_count": 1288,
         "received_count": 1288, "complete": True},
    ],
}
PHOTO = {"run_id": "20261127T032004-71be0d", "bronze_run_id": RUN, "status": "ok", "generated_at": "2026-11-27T03:20:04+09:00",
         "updated_at": "2026-11-27T03:41:10+09:00", "counts": {"stored": 212, "failed": 2},
         "failed": [{"desertionNo": "448567202600131", "slot": 2, "status": "failed", "reason": "not_found", "attempts": 1},
                    {"desertionNo": "448567202600132", "slot": 1, "status": "gave_up", "reason": "timeout", "attempts": 3}]}


def test_bronze_and_photo_rows():
    stage, failures = ops_summary.bronze_rows("test", BRONZE, manifest_key="k")
    assert (stage["status"], stage["input_count"], stage["output_count"]) == ("warn", 8700, 8697)
    assert [f["reason_code"] for f in failures] == ["count_mismatch", "dropped_row"]
    assert failures[0]["subject_key"] == "list_protect 20261120~20261127"
    stage, failures = ops_summary.photo_rows("test", PHOTO)
    assert stage["stage"] == "photo" and stage["output_count"] == 212
    assert [(f["subject_key"], f["reason_code"]) for f in failures] == [
        ("448567202600131#2", "not_found"), ("448567202600132#1", "gave_up")]
    aborted, fl = ops_summary.bronze_rows("test", {**BRONZE, "status": "aborted", "partitions": [], "dropped_count": 0})
    assert aborted["status"] == "failed" and fl[0]["reason_code"] == "aborted" and fl[0]["blocking"] is True


def test_silver_rows_blocking_conflicts_only():
    stats = {"ready": False, "stage": "NOTICE_DELTA", "observed_at": AT, "conflicts": [
        {"desertion_no": "448567202600142", "sources": ["list_protect", "updated"], "reason": "raw_content_conflict"}]}
    stage, failures = ops_summary.silver_notice_rows("test", RUN, stats)
    assert stage["status"] == "failed"
    assert [(f["subject_key"], f["blocking"]) for f in failures] == [("448567202600142", True)]
    ok, none = ops_summary.silver_notice_rows("test", RUN, {"ready": True, "insert_count": 286, "update_count": 512,
                                                            "parse_hold": {"weight_parse_status": {"ambiguous": 61}}})
    assert ok["status"] == "warn" and ok["output_count"] == 798 and none == []  # 값 보류는 실패 줄이 아님


def test_gold_rows_counts_and_cost():
    stages = ops_summary.gold_rows("test", RUN,
        [{"gold_run_id": "g1", "task": "photo_trait", "version": "photo_trait@1", "publish_id": f"silver_{RUN}",
          "started_at": "2026-11-27T04:02:11+09:00"}],
        [{"gold_run_id": "g1", "status": "OK"}, {"gold_run_id": "g1", "status": "OK"},
         {"gold_run_id": "g1", "status": "FAILED"}, {"gold_run_id": "other", "status": "OK"}],
        [{"gold_run_id": "g1", "status": "OK", "cost_usd": 0.003, "latency_ms": 1000, "requested_model": "m", "response_model": "m"},
         {"gold_run_id": "g1", "status": "ERROR", "cost_usd": None, "latency_ms": 3000, "requested_model": "m", "response_model": "n"}])
    (s,) = stages
    assert (s["stage"], s["status"], s["input_count"], s["output_count"], s["failed_count"]) == ("gold_photo_trait", "warn", 3, 2, 1)
    assert s["stats"]["calls"] == 2 and s["stats"]["call_errors"] == 1 and s["stats"]["cost_usd"] == 0.003
    assert s["stats"]["models"] == {"m → m": 1, "m → n": 1}


class FakeS3:
    def __init__(self, objects):
        self.objects = objects

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(f"NoSuchKey {Key}")
        return {"Body": io.BytesIO(json.dumps(self.objects[Key]).encode())}

    def get_paginator(self, name):
        s3 = self

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k} for k in s3.objects if k.startswith(Prefix)]}

        return P()


def test_collect_s3_marks_missing_stages_skipped():
    s3 = FakeS3({
        f"bronze/rescue/raw/dt=2026-11-27/run_id={RUN}/manifest.json": BRONZE,
        "bronze/rescue/image_runs/dt=2026-11-27/run_id=20261127T032004-71be0d/manifest.json": PHOTO,
        "bronze/rescue/image_runs/dt=2026-11-27/run_id=x/manifest.json": {**PHOTO, "bronze_run_id": "other"},
    })
    stages, failures = ops_summary.collect_s3(s3, "b", "test", RUN)
    assert [(s["stage"], s["status"]) for s in stages] == [
        ("bronze", "warn"), ("photo", "warn"), ("silver_notice", "skipped"), ("silver_publish", "skipped")]
    assert len(failures) == 4


def test_main_without_run_id_is_quiet(capsys):
    assert ops_summary.main(["--run-id", "None"]) == 0
    assert "수집 실행 id 없음" in capsys.readouterr().out


# ───────── RDS 동기화 기록 줄 ─────────

def test_sync_ops_rows():
    sync_cli = pytest.importorskip("rebom.serving.sync.cli")
    from rebom.serving.sync import db

    args = sync_cli.build_parser().parse_args(["tag", "--publish-id", f"silver_{RUN}"])
    (stage,), fails = sync_cli.ops_rows(args, AT, result={"publish_id": f"silver_{RUN}", "publish_seq": 43})
    assert (stage["stage"], stage["bronze_run_id"], stage["status"], fails) == ("rds_tag", RUN, "ok", [])
    (stage,), (fail,) = sync_cli.ops_rows(args, AT, exc=db.StaleInput("older"))
    assert (stage["status"], fail["reason_code"], fail["blocking"]) == ("warn", "stale_input", False)
    _, (fail,) = sync_cli.ops_rows(args, AT, exc=RuntimeError("active_version.config_hash 가 다릅니다"))
    assert fail["reason_code"] == "config_hash_mismatch" and fail["blocking"] is True
    dry = sync_cli.build_parser().parse_args(["tag", "--dry-run", "--publish-id", f"silver_{RUN}"])
    assert sync_cli.ops_rows(dry, AT, result={}) == ([], [])


# ───────── DAG ─────────

def test_dag_has_ops_summary_last():
    tree = ast.parse((ROOT / "dags" / "rescue_ingest.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "KubernetesPodOperator"]
    kw = next({k.arg: k.value for k in c.keywords} for c in calls
              if any(k.arg == "task_id" and getattr(k.value, "value", None) == "write_ops_summary" for k in c.keywords))
    assert ast.literal_eval(kw["trigger_rule"]) == "all_done"
    assert ast.literal_eval(kw["cmds"]) == ["python", "-m", "rebom.common.ops_summary"]
    assert "write_ops_summary" in ast.unparse(tree).split("[task for task in dag.tasks if task is not write_ops_summary]")[1][:40]
