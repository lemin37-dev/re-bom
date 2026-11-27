""".github/scripts/migration_guard.py: 이미 있는 마이그레이션 수정을 PR 에서 막는지. git 부분은 임시 저장소로 확인한다."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("migration_guard", ROOT / ".github" / "scripts" / "migration_guard.py")
mg = importlib.util.module_from_spec(_spec)
sys.modules["migration_guard"] = mg
_spec.loader.exec_module(mg)

BASE = ["001_init.sql", "002_x.sql", "README.md"]


def test_adding_next_number_passes():
    assert mg.problems(["A\tdb/migrations/003_ops.sql"], BASE, BASE + ["003_ops.sql"]) == []


@pytest.mark.parametrize("line, word", [
    ("M\tdb/migrations/001_init.sql", "수정"),
    ("D\tdb/migrations/002_x.sql", "삭제"),
    ("R100\tdb/migrations/002_x.sql\tdb/migrations/003_x.sql", "이름 변경"),
])
def test_touching_existing_file_fails(line, word):
    errs = mg.problems([line], BASE, BASE)
    assert len(errs) == 1 and word in errs[0]


@pytest.mark.parametrize("name, word", [
    ("002_late.sql", "보다 큰 번호"),
    ("3_bad.sql", "형식"),
    ("003_Bad-Name.sql", "형식"),
])
def test_bad_new_file_fails(name, word):
    errs = mg.problems([f"A\tdb/migrations/{name}"], BASE, BASE + [name])
    assert any(word in e for e in errs)


def test_duplicate_number_fails_and_non_sql_ignored():
    head = BASE + ["003_a.sql", "003_b.sql"]
    errs = mg.problems(["A\tdb/migrations/003_a.sql", "A\tdb/migrations/003_b.sql", "M\tdb/migrations/README.md"],
                       BASE, head)
    assert errs == ["번호 003 이 여러 파일에 있습니다"]


def test_other_folders_ignored():
    assert mg.problems(["M\tdocs/schema/rds.dbml", "M\tsrc/rebom/common/migrate.py"], BASE, BASE) == []


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def test_main_against_git_history(tmp_path, monkeypatch, capsys):
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git 없음")
    repo = tmp_path / "repo"
    mig = repo / "db" / "migrations"
    mig.mkdir(parents=True)
    _run(repo, "git", "init", "-q", "-b", "develop")
    _run(repo, "git", "config", "user.email", "t@example.invalid")
    _run(repo, "git", "config", "user.name", "t")
    (mig / "001_init.sql").write_text("CREATE TABLE a (id int);\n", encoding="utf-8")
    _run(repo, "git", "add", ".")
    _run(repo, "git", "commit", "-q", "-m", "base")
    _run(repo, "git", "switch", "-q", "-c", "feat/x")
    monkeypatch.setattr(mg, "ROOT", repo)

    (mig / "002_next.sql").write_text("CREATE TABLE b (id int);\n", encoding="utf-8")
    _run(repo, "git", "add", ".")
    _run(repo, "git", "commit", "-q", "-m", "add")
    assert mg.main(["develop"]) == 0
    assert "002_next.sql" in capsys.readouterr().out

    (mig / "001_init.sql").write_text("CREATE TABLE a (id bigint);\n", encoding="utf-8")
    _run(repo, "git", "commit", "-q", "-am", "edit")
    assert mg.main(["develop"]) == 1
    assert "001_init.sql: 이미 있는 마이그레이션 수정" in capsys.readouterr().out


def test_repo_migrations_are_well_named():
    names = [p.name for p in (ROOT / "db" / "migrations").glob("*.sql")]
    assert names and all(mg.NAME.match(n) for n in names), names
    assert mg.problems([], names, names) == []
