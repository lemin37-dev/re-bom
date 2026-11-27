"""RDS 스키마 버전 태그(schema-vX.Y)를 docs/schema/rds.dbml 머리 줄 기준으로 확인하고 단다.

머리 줄 형식:  // 버전: 스키마 v0.1 = 마이그레이션 001~005. …
- check : 머리 줄의 마지막 번호가 db/migrations 의 마지막 번호와 같은지, 같은 이름의 태그가 이미 있으면 범위가 같은지 본다.
          (PR 에서 실행. 이미 v0.1 태그가 001~005 로 달렸는데 006 을 v0.1 로 넣으려 하면 실패 → 버전 이름을 올려야 함)
- tag   : develop 에 머지된 뒤 실행. 태그가 없으면 지금 커밋에 annotated 태그를 달아 push, 있으면 범위만 확인.
          동시에 두 번 돌아도 push 는 한 번만 성공하고, 진 쪽은 태그를 다시 읽어 범위를 확인한다.
태그 메시지 첫 줄: "RDS 스키마 v0.1 (001~005)"
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DBML = ROOT / "docs" / "schema" / "rds.dbml"
MIGRATIONS = ROOT / "db" / "migrations"
HEADER = re.compile(r"^// 버전: 스키마 (v\d+\.\d+) = 마이그레이션 (\d{3})~(\d{3})\b", re.M)
MESSAGE = re.compile(r"^RDS 스키마 (v\d+\.\d+) \((\d{3})~(\d{3})\)")


@dataclass(frozen=True)
class SchemaVersion:
    name: str    # v0.1
    first: str   # 001
    last: str    # 005

    @property
    def tag(self) -> str:
        return f"schema-{self.name}"

    @property
    def message(self) -> str:
        return f"RDS 스키마 {self.name} ({self.first}~{self.last})"


def read_header(text: str) -> SchemaVersion:
    m = HEADER.search(text)
    if not m:
        raise ValueError("docs/schema/rds.dbml 에 '// 버전: 스키마 vX.Y = 마이그레이션 NNN~NNN' 줄이 없습니다")
    return SchemaVersion(*m.groups())


def last_migration(directory: Path | None = None) -> str:
    nums = sorted(p.name[:3] for p in (directory or MIGRATIONS).glob("*.sql") if p.name[:3].isdigit())
    if not nums:
        raise ValueError("db/migrations 에 파일이 없습니다")
    return nums[-1]


def parse_message(message: str) -> SchemaVersion | None:
    m = MESSAGE.match(message.strip())
    return SchemaVersion(*m.groups()) if m else None


def problems(header: SchemaVersion, last: str, existing: SchemaVersion | None) -> list[str]:
    out = []
    if header.first != "001":
        out.append(f"범위는 001 부터여야 합니다: {header.first}")
    if header.last != last:
        out.append(f"머리 줄은 {header.last} 까지인데 마이그레이션은 {last} 까지 있습니다. 머리 줄을 고쳐 주세요")
    if existing is not None and (existing.first, existing.last) != (header.first, header.last):
        out.append(f"{header.tag} 태그는 이미 {existing.first}~{existing.last} 로 달려 있습니다. "
                   f"마이그레이션이 늘었으면 버전 이름을 올려 주세요(예: v0.1 → v0.2)")
    return out


# ───────── git ─────────

def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=check)


def remote_tag(tag: str) -> SchemaVersion | None:
    """origin 의 태그를 받아 메시지를 읽는다. 없으면 None."""
    if not _git("ls-remote", "--tags", "origin", f"refs/tags/{tag}").stdout.strip():
        return None
    _git("fetch", "--force", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    msg = _git("tag", "-l", "--format=%(contents)", tag).stdout
    found = parse_message(msg)
    if found is None:
        raise ValueError(f"{tag} 태그 메시지 형식이 다릅니다: {msg.strip()[:80]!r}")
    return found


def create_tag(v: SchemaVersion) -> bool:
    """지금 커밋에 태그를 달고 push. 다른 실행이 먼저 달았으면 False."""
    _git("tag", "-a", v.tag, "-m", v.message, "HEAD")
    pushed = _git("push", "origin", f"refs/tags/{v.tag}", check=False)
    if pushed.returncode == 0:
        return True
    _git("tag", "-d", v.tag, check=False)
    if remote_tag(v.tag) is None:
        raise RuntimeError(f"태그 push 실패: {pushed.stderr.strip()}")
    return False


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in ("check", "tag"):
        print("사용법: schema_tag.py check|tag", file=sys.stderr)
        return 64
    header = read_header(DBML.read_text(encoding="utf-8"))
    existing = remote_tag(header.tag)
    errs = problems(header, last_migration(), existing)
    if errs:
        for e in errs:
            print(f"::error::{e}")
        return 1
    if argv[0] == "check" or existing is not None:
        print(f"{header.tag}: {'이미 있음, 범위 같음' if existing else '아직 없음(머지 뒤 생성)'} — {header.message}")
        return 0
    if create_tag(header):
        print(f"태그 생성: {header.tag} — {header.message}")
        return 0
    again = remote_tag(header.tag)
    errs = problems(header, last_migration(), again)
    for e in errs:
        print(f"::error::{e}")
    print(f"{header.tag}: 다른 실행이 먼저 만듦" + ("" if errs else ", 범위 같음"))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
