"""RDS(rebom DB) 마이그레이션 적용 도구. db/migrations/NNN_*.sql 을 번호 순으로 적용하고 schema_migrations 에 기록한다.

    python -m rebom.common.migrate status                 # 적용된 파일, 남은 파일, 내용이 바뀐 파일
    python -m rebom.common.migrate apply --dry-run        # 적용할 파일만 보여 줌
    python -m rebom.common.migrate apply --by lemin       # 남은 파일을 순서대로 적용
    python -m rebom.common.migrate baseline 004 --by lemin  # 표가 생기기 전에 손으로 적용한 001~004 의 기록만 넣음

- 파일 하나 = 트랜잭션 하나. 파일 실행과 기록 INSERT 가 함께 커밋되고, 실패하면 둘 다 되돌린다.
  그래서 마이그레이션 파일 안에는 BEGIN/COMMIT 을 쓰지 않는다. 트랜잭션 밖에서만 되는 명령
  (CREATE INDEX CONCURRENTLY 등)이 필요하면 파일 첫 줄에 `-- migrate: no-transaction` 을 적는다.
- 이미 적용한 파일의 내용(sha256)이 레포와 다르면 apply 를 멈춘다(AGENTS.md 2번: 적용한 파일은 고치지 않는다).
- 적용은 사람이 한다(AGENTS.md 5번). 접속은 Secrets Manager rebom/dev/rebom-db, 내 PC 에서는 SSM 터널 뒤
  REBOM_DB_HOST=127.0.0.1, REBOM_DB_PORT=<로컬 포트>.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "db" / "migrations"
DEFAULT_DB_SECRET_ID = "rebom/dev/rebom-db"
NO_TRANSACTION = "-- migrate: no-transaction"
_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")

# 005_schema_migrations.sql 과 같은 정의 (tests/test_migrate.py 가 같은지 확인)
BOOTSTRAP_SQL = """CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    text         PRIMARY KEY,
    sha256      char(64)     NOT NULL,
    applied_at  timestamptz  NOT NULL DEFAULT now(),
    applied_by  text         NOT NULL,
    how         varchar(10)  NOT NULL CHECK (how IN ('apply', 'baseline'))
);"""


@dataclass(frozen=True)
class Migration:
    number: int
    filename: str
    sql: str
    sha256: str

    @property
    def transactional(self) -> bool:
        return not self.sql.lstrip().startswith(NO_TRANSACTION)


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    out, seen = [], {}
    for path in sorted(directory.glob("*.sql")):
        m = _NAME.match(path.name)
        if not m:
            raise ValueError(f"마이그레이션 파일 이름은 NNN_소문자_설명.sql 이어야 합니다: {path.name}")
        number = int(m.group(1))
        if number in seen:
            raise ValueError(f"같은 번호가 두 개: {seen[number]}, {path.name}")
        seen[number] = path.name
        raw = path.read_bytes().replace(b"\r\n", b"\n")  # 줄바꿈이 CRLF 로 풀려도 같은 값
        out.append(Migration(number, path.name, raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()))
    return out


@dataclass
class Plan:
    applied: list[str]
    pending: list[Migration]
    changed: list[str]   # 적용 기록과 내용이 다른 파일
    missing: list[str]   # 기록은 있는데 레포에 없는 파일


def make_plan(migrations: list[Migration], applied: dict[str, str]) -> Plan:
    names = {m.filename for m in migrations}
    plan = Plan(applied=[], pending=[], changed=[], missing=sorted(set(applied) - names))
    for m in migrations:
        if m.filename not in applied:
            plan.pending.append(m)
        elif applied[m.filename] != m.sha256:
            plan.changed.append(m.filename)
        else:
            plan.applied.append(m.filename)
    if plan.pending and plan.applied:
        last = max(int(n[:3]) for n in plan.applied + plan.changed)
        early = [m.filename for m in plan.pending if m.number < last]
        if early:
            raise ValueError(f"이미 적용한 번호보다 앞 번호가 아직 안 됨(번호 순서가 꼬임): {early}")
    return plan


def baseline_targets(migrations: list[Migration], applied: dict[str, str], through: int) -> list[Migration]:
    if through not in {m.number for m in migrations}:
        raise ValueError(f"{through:03d} 번 파일이 없습니다")
    return [m for m in migrations if m.number <= through and m.filename not in applied]


# ───────── DB ─────────

def db_params() -> dict[str, Any]:
    import boto3

    secret_id = os.environ.get("REBOM_DB_SECRET_ID", DEFAULT_DB_SECRET_ID)
    region = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
    s = json.loads(boto3.client("secretsmanager", region_name=region).get_secret_value(SecretId=secret_id)["SecretString"])
    return {
        "host": os.environ.get("REBOM_DB_HOST") or s["host"],
        "port": int(os.environ.get("REBOM_DB_PORT") or s.get("port", 5432)),
        "dbname": s["dbname"],
        "user": s["username"],
        "password": s["password"],
        "sslmode": os.environ.get("REBOM_DB_SSLMODE", "require"),  # RDS rds.force_ssl=1
        "connect_timeout": 10,
    }


def connect():
    import psycopg

    return psycopg.connect(**db_params(), autocommit=True)


def table_exists(conn) -> bool:
    return conn.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL").fetchone()[0]


def read_applied(conn) -> dict[str, str]:
    if not table_exists(conn):
        return {}
    return dict(conn.execute("SELECT filename, sha256 FROM schema_migrations").fetchall())


def _record(conn, m: Migration, by: str, how: str) -> None:
    conn.execute(
        "INSERT INTO schema_migrations (filename, sha256, applied_by, how) VALUES (%s, %s, %s, %s)",
        (m.filename, m.sha256, by, how),
    )


def apply_one(conn, m: Migration, by: str) -> None:
    if m.transactional:
        with conn.transaction():
            conn.execute(m.sql)
            _record(conn, m, by, "apply")
    else:
        conn.execute(m.sql)
        _record(conn, m, by, "apply")


# ───────── CLI ─────────

def _print_plan(plan: Plan) -> None:
    print(f"적용됨 {len(plan.applied)}: {', '.join(plan.applied) or '-'}")
    print(f"남음 {len(plan.pending)}: {', '.join(m.filename for m in plan.pending) or '-'}")
    if plan.changed:
        print(f"⚠ 적용 뒤 내용이 바뀐 파일: {', '.join(plan.changed)}")
    if plan.missing:
        print(f"⚠ 기록은 있는데 레포에 없는 파일: {', '.join(plan.missing)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rebom.common.migrate", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="적용 상태 보기")
    a = sub.add_parser("apply", help="남은 파일을 번호 순으로 적용")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--by", default=None, help="적용한 사람 (기본: PC 사용자 이름)")
    b = sub.add_parser("baseline", help="이미 손으로 적용한 파일들의 기록만 넣기")
    b.add_argument("through", type=int, help="이 번호까지 (예: 4)")
    b.add_argument("--by", default=None)
    args = ap.parse_args(argv)

    migrations = load_migrations()
    with connect() as conn:
        if args.cmd == "status":
            if not table_exists(conn):
                print("schema_migrations 표 없음 (아직 기록 전). 손으로 적용한 파일이 있으면 baseline 부터")
            _print_plan(make_plan(migrations, read_applied(conn)))
            return 0

        by = args.by or getpass.getuser()
        if args.cmd == "baseline":
            conn.execute(BOOTSTRAP_SQL)
            targets = baseline_targets(migrations, read_applied(conn), args.through)
            with conn.transaction():
                for m in targets:
                    _record(conn, m, by, "baseline")
            print(f"기록만 넣음 {len(targets)}: {', '.join(m.filename for m in targets) or '-'}")
            return 0

        plan = make_plan(migrations, read_applied(conn))
        _print_plan(plan)
        if plan.changed or plan.missing:
            print("멈춤: 적용한 파일은 고치지 않고 새 번호 파일을 추가해 주세요")
            return 2
        if args.dry_run or not plan.pending:
            return 0
        conn.execute(BOOTSTRAP_SQL)
        for m in plan.pending:
            apply_one(conn, m, by)
            print(f"적용 {m.filename}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
