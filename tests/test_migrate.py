"""rebom.common.migrate: 파일 목록·순서·내용 변경 판정 (DB 없이)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from rebom.common import migrate

ROOT = Path(__file__).resolve().parents[1]


def _write(d: Path, name: str, sql: str = "SELECT 1;\n") -> None:
    (d / name).write_text(sql, encoding="utf-8", newline="\n")


def test_repo_migrations_load_in_order():
    ms = migrate.load_migrations()
    assert [m.number for m in ms] == sorted(m.number for m in ms)
    assert ms[0].filename == "001_init.sql"
    assert all(len(m.sha256) == 64 for m in ms)


def test_repo_migrations_have_no_transaction_statements():
    # 도구가 파일 단위로 트랜잭션을 묶으므로 파일 안의 BEGIN/COMMIT 은 금지
    for m in migrate.load_migrations():
        body = re.sub(r"--[^\n]*", "", m.sql).upper()
        assert not re.search(r"^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", body, re.M), m.filename


def test_bootstrap_matches_005():
    sql = (ROOT / "db" / "migrations" / "005_schema_migrations.sql").read_text(encoding="utf-8")
    assert migrate.BOOTSTRAP_SQL in sql


def test_bad_name_and_duplicate_number(tmp_path):
    _write(tmp_path, "001_init.sql")
    _write(tmp_path, "1_bad.sql")
    with pytest.raises(ValueError, match="이름"):
        migrate.load_migrations(tmp_path)
    (tmp_path / "1_bad.sql").unlink()
    _write(tmp_path, "001_other.sql")
    with pytest.raises(ValueError, match="같은 번호"):
        migrate.load_migrations(tmp_path)


def test_plan_pending_changed_missing(tmp_path):
    for n in ("001_a.sql", "002_b.sql", "003_c.sql"):
        _write(tmp_path, n)
    ms = migrate.load_migrations(tmp_path)
    sha = {m.filename: m.sha256 for m in ms}
    plan = migrate.make_plan(ms, {"001_a.sql": sha["001_a.sql"], "002_b.sql": "0" * 64, "000_old.sql": "x"})
    assert plan.applied == ["001_a.sql"]
    assert plan.changed == ["002_b.sql"]
    assert [m.filename for m in plan.pending] == ["003_c.sql"]
    assert plan.missing == ["000_old.sql"]


def test_plan_refuses_gap_before_applied(tmp_path):
    for n in ("001_a.sql", "002_b.sql"):
        _write(tmp_path, n)
    ms = migrate.load_migrations(tmp_path)
    with pytest.raises(ValueError, match="순서"):
        migrate.make_plan(ms, {"002_b.sql": ms[1].sha256})


def test_baseline_targets(tmp_path):
    for n in ("001_a.sql", "002_b.sql", "003_c.sql"):
        _write(tmp_path, n)
    ms = migrate.load_migrations(tmp_path)
    got = migrate.baseline_targets(ms, {"001_a.sql": ms[0].sha256}, 2)
    assert [m.filename for m in got] == ["002_b.sql"]
    with pytest.raises(ValueError):
        migrate.baseline_targets(ms, {}, 9)


def test_no_transaction_marker(tmp_path):
    _write(tmp_path, "001_a.sql", "-- migrate: no-transaction\nCREATE INDEX CONCURRENTLY x ON t (c);\n")
    assert not migrate.load_migrations(tmp_path)[0].transactional
