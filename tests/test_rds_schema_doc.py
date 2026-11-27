"""docs/rds_schema.dbml(설명 원본)과 db/migrations/*.sql(실제 DB)의 표·칼럼 이름이 같은지 본다.

DBML 은 dbdiagram.io 에 붙여 넣어 그림·설명을 보는 문서이고, 실제 DB 는 마이그레이션 파일이 만든다.
둘 중 하나만 고치면 이 테스트가 실패한다. 벡터 표의 version 키처럼 아직 마이그레이션이 없는 목표 칼럼은
PENDING 에 적어 두고, 해당 마이그레이션을 추가할 때 지운다.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DBML = ROOT / "docs" / "rds_schema.dbml"
MIGRATIONS = sorted((ROOT / "db" / "migrations").glob("*.sql"))

# DBML 에는 있고 마이그레이션에는 아직 없는 목표 칼럼 (다음 번호 마이그레이션에서 추가)
PENDING: dict[str, set[str]] = {}
# 마이그레이션에는 있지만 목표 모양에서 빠지는 칼럼
DROPPED: dict[str, set[str]] = {}

_CONSTRAINT_WORDS = {"PRIMARY", "CONSTRAINT", "UNIQUE", "CHECK", "FOREIGN", "EXCLUDE"}


def dbml_tables(text: str) -> dict[str, list[str]]:
    tables = {}
    for m in re.finditer(r"^Table (\w+)[^{]*\{\n(.*?)^\}", text, re.S | re.M):
        cols = []
        for line in m.group(2).splitlines():
            cm = re.match(r"  (\w+) \S", line)
            if cm and cm.group(1) != "indexes":
                cols.append(cm.group(1))
        tables[m.group(1)] = cols
    return tables


def migration_tables(texts: list[str]) -> dict[str, list[str]]:
    tables = {}
    for text in texts:
        text = re.sub(r"--[^\n]*", "", text)
        for m in re.finditer(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\((.*?)\n\);", text, re.S):
            cols = []
            for line in m.group(2).splitlines():
                cm = re.match(r"\s+([a-z_][a-z0-9_]*)\s", line)
                if cm and cm.group(1).upper() not in _CONSTRAINT_WORDS:
                    cols.append(cm.group(1))
            tables[m.group(1)] = cols
        # 뒤 번호 파일의 ALTER TABLE … ADD/DROP COLUMN 을 반영
        for m in re.finditer(r"ALTER TABLE (?:IF EXISTS )?(\w+)\s+(.*?);", text, re.S):
            cols = tables.setdefault(m.group(1), [])
            for action, col in re.findall(r"(ADD|DROP) COLUMN (?:IF (?:NOT )?EXISTS )?(\w+)", m.group(2), re.I):
                if action.upper() == "ADD" and col not in cols:
                    cols.append(col)
                elif action.upper() == "DROP" and col in cols:
                    cols.remove(col)
    return tables


def test_parsers_find_tables():
    assert len(dbml_tables(DBML.read_text(encoding="utf-8"))) == 11
    assert "service_notice" in migration_tables([p.read_text(encoding="utf-8") for p in MIGRATIONS])


def test_dbml_matches_migrations():
    doc = dbml_tables(DBML.read_text(encoding="utf-8"))
    db = migration_tables([p.read_text(encoding="utf-8") for p in MIGRATIONS])
    assert set(doc) == set(db), f"표 목록이 다름: 문서만 {set(doc) - set(db)}, 마이그레이션만 {set(db) - set(doc)}"
    for name, cols in doc.items():
        expected = set(db[name]) | PENDING.get(name, set())
        expected -= DROPPED.get(name, set())
        assert set(cols) == expected, (
            f"{name}: 문서만 {sorted(set(cols) - expected)}, 마이그레이션만 {sorted(expected - set(cols))}"
        )


def test_every_column_has_note():
    text = DBML.read_text(encoding="utf-8")
    for m in re.finditer(r"^Table (\w+)[^{]*\{\n(.*?)^\}", text, re.S | re.M):
        for line in m.group(2).splitlines():
            if re.match(r"  \w+ \S", line) and not line.startswith("  indexes"):
                assert "note:" in line, f"{m.group(1)}: 설명 없는 칼럼 {line.strip()}"


def test_sql_files_are_lf_utf8():
    for path in MIGRATIONS + [DBML]:
        raw = path.read_bytes()
        raw.decode("utf-8")
        assert b"\r\n" not in raw, f"{path.name}: CRLF"
