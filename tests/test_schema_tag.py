""".github/scripts/schema_tag.py: 스키마 버전 머리 줄과 태그 판정. git 부분은 임시 저장소로 확인한다."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("schema_tag", ROOT / ".github" / "scripts" / "schema_tag.py")
st = importlib.util.module_from_spec(_spec)
sys.modules["schema_tag"] = st
_spec.loader.exec_module(st)


def test_repo_header_matches_migrations():
    header = st.read_header(st.DBML.read_text(encoding="utf-8"))
    assert st.problems(header, st.last_migration(), None) == []


def test_header_and_message_round_trip():
    v = st.read_header("// 버전: 스키마 v0.2 = 마이그레이션 001~007. 설명\n")
    assert (v.tag, v.message) == ("schema-v0.2", "RDS 스키마 v0.2 (001~007)")
    assert st.parse_message(v.message + "\n\n본문") == v
    with pytest.raises(ValueError):
        st.read_header("// 버전 없음")


def test_problems():
    v1 = st.SchemaVersion("v0.1", "001", "005")
    assert st.problems(v1, "005", v1) == []
    assert "머리 줄" in st.problems(v1, "006", None)[0]
    grown = st.SchemaVersion("v0.1", "001", "006")
    assert "버전 이름을 올려" in st.problems(grown, "006", v1)[0]
    assert st.problems(st.SchemaVersion("v0.2", "001", "006"), "006", None) == []


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    work = tmp_path / "work"
    (work / "db" / "migrations").mkdir(parents=True)
    (work / "docs" / "schema").mkdir(parents=True)
    _git(tmp_path, "init", "-q", str(work))
    for k, v in (("user.name", "t"), ("user.email", "t@example.com")):
        _git(work, "config", k, v)
    _git(work, "remote", "add", "origin", str(remote))
    monkeypatch.setattr(st, "ROOT", work)
    monkeypatch.setattr(st, "DBML", work / "docs" / "schema" / "rds.dbml")
    monkeypatch.setattr(st, "MIGRATIONS", work / "db" / "migrations")
    return work


def _commit(work: Path, version: str, last: int) -> None:
    for n in range(1, last + 1):
        (work / "db" / "migrations" / f"{n:03d}_m.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (work / "docs" / "schema" / "rds.dbml").write_text(f"// 버전: 스키마 {version} = 마이그레이션 001~{last:03d}. x\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", f"{version} {last}")


def test_tag_flow(repo):
    _commit(repo, "v0.1", 5)
    assert st.main(["check"]) == 0
    assert st.main(["tag"]) == 0
    assert st.remote_tag("schema-v0.1") == st.SchemaVersion("v0.1", "001", "005")
    # 문서만 고친 머지: 태그 그대로, 성공
    assert st.main(["tag"]) == 0
    # 006 을 넣고 이름을 안 올림: PR 확인에서 실패
    _commit(repo, "v0.1", 6)
    assert st.main(["check"]) == 1
    assert st.main(["tag"]) == 1
    # 이름을 올리면 새 태그
    _commit(repo, "v0.2", 6)
    assert st.main(["tag"]) == 0
    assert st.remote_tag("schema-v0.2").last == "006"


def test_lost_race_reads_existing(repo):
    _commit(repo, "v0.1", 5)
    v = st.SchemaVersion("v0.1", "001", "005")
    assert st.create_tag(v) is True
    # 다른 실행이 먼저 push 한 상황: 다른 커밋에서 같은 이름으로 달면 push 가 거절되어 False, 로컬 태그도 정리
    _git(repo, "tag", "-d", "schema-v0.1")
    (repo / "docs" / "note.txt").write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "doc")
    assert st.create_tag(v) is False
    assert st.remote_tag("schema-v0.1") == v  # 먼저 단 태그가 그대로
    assert st.remote_tag("schema-v0.1") == v
