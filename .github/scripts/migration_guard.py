"""이미 있는 마이그레이션 파일을 고치지 못하게 막는다 (PR 에서 실행).

규칙(AGENTS.md 2번): db/migrations/*.sql 은 한 번 들어가면 고치지 않고 다음 번호 파일을 추가한다.
`migrate apply` 도 적용한 파일의 sha256 이 바뀌면 멈추지만, 그건 RDS 에 적용할 때다. 이 검사는 PR 단계에서 먼저 막는다.

  python3 .github/scripts/migration_guard.py origin/develop

기준 브랜치와 비교해 db/migrations/ 아래에서
- 이미 있는 파일의 수정·삭제·이름 변경 → 실패
- 새 파일이 기준의 마지막 번호보다 작거나 같은 번호, 번호 중복, 이름 형식(NNN_이름.sql) 위반 → 실패
README 처럼 .sql 이 아닌 파일은 보지 않는다.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIR = "db/migrations/"
NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=True).stdout


def problems(name_status: list[str], base_files: list[str], head_files: list[str]) -> list[str]:
    """name_status: `git diff --name-status -M` 줄. base_files/head_files: 양쪽의 db/migrations 파일 이름."""
    errs: list[str] = []
    base_sql = [f for f in base_files if f.endswith(".sql")]
    base_last = max((int(f[:3]) for f in base_sql if f[:3].isdigit()), default=0)
    for line in name_status:
        parts = line.split("\t")
        status, paths = parts[0], parts[1:]
        if not any(p.startswith(DIR) and p.endswith(".sql") for p in paths):
            continue
        if status == "A":
            name = Path(paths[0]).name
            m = NAME.match(name)
            if not m:
                errs.append(f"{name}: 이름은 NNN_소문자_설명.sql 형식이어야 합니다")
            elif int(m.group(1)) <= base_last:
                errs.append(f"{name}: 기준의 마지막 번호 {base_last:03d} 보다 큰 번호여야 합니다")
            continue
        kind = {"M": "수정", "D": "삭제", "T": "형식 변경"}.get(status[0], "이름 변경" if status[0] == "R" else status)
        errs.append(f"{' → '.join(paths)}: 이미 있는 마이그레이션 {kind}. 고치지 말고 다음 번호 파일을 추가하세요")
    nums = [f[:3] for f in head_files if f.endswith(".sql") and f[:3].isdigit()]
    for dup in sorted({n for n in nums if nums.count(n) > 1}):
        errs.append(f"번호 {dup} 이 여러 파일에 있습니다")
    return errs


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("사용법: migration_guard.py <기준 ref, 예: origin/develop>", file=sys.stderr)
        return 64
    base = _git("merge-base", argv[0], "HEAD").strip()
    name_status = [l for l in _git("diff", "--name-status", "-M", base, "HEAD", "--", DIR).splitlines() if l]
    base_files = [Path(p).name for p in _git("ls-tree", "--name-only", base, DIR).splitlines()]
    head_files = [Path(p).name for p in _git("ls-tree", "--name-only", "HEAD", DIR).splitlines()]
    errs = problems(name_status, base_files, head_files)
    for e in errs:
        print(f"::error::{e}")
    if not errs:
        added = [l.split("\t")[1] for l in name_status if l.startswith("A")]
        print("마이그레이션 검사 통과" + (f": 추가 {', '.join(Path(a).name for a in added)}" if added else ": 변경 없음"))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
