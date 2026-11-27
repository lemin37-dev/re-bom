"""운영 화면 탭2 가 비교할 "문서상 스키마"를 만든다: src/rebom/serving/ops_schema_catalog.json (커밋한다).

API 이미지에는 docs/·db/ 가 없어서, DBML 의 표·칼럼과 마이그레이션 파일 sha256 을 이 JSON 으로 옮겨 API 가 읽는다.
화면은 이것을 실제 값(ops_schema_snapshot 의 Silver·Gold 칼럼, RDS information_schema·schema_migrations)과 비교한다.
DBML·마이그레이션을 고치면 다시 만든다. 안 만들면 tests/test_ops_api.py 가 실패한다.

    python scripts/schema/build_schema_catalog.py           # 다시 만들기
    python scripts/schema/build_schema_catalog.py --check   # 최신인지만 (CI)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "src" / "rebom" / "serving" / "ops_schema_catalog.json"
LAYERS = (
    ("bronze", ROOT / "docs" / "schema" / "bronze.dbml"),
    ("silver", ROOT / "docs" / "schema" / "silver.dbml"),
    ("gold", ROOT / "docs" / "schema" / "gold.dbml"),
    ("rds", ROOT / "docs" / "schema" / "rds.dbml"),
)
MIGRATIONS = ROOT / "db" / "migrations"
TABLE = re.compile(r"^Table (\w+)[^{]*\{\n(.*?)^\}", re.S | re.M)
COLUMN = re.compile(r"^  (\w+) (\S+)")
HEADER = re.compile(r"^// 버전: 스키마 (v\d+\.\d+) = 마이그레이션 (\d{3})~(\d{3})", re.M)


def fingerprint(columns: list[list[str]]) -> str:
    """rebom.common.ops_log.fingerprint 와 같은 방식 (칼럼 이름·타입, 순서 무관)."""
    pairs = sorted((str(n), str(t).lower()) for n, t in columns)
    return hashlib.sha256(json.dumps(pairs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def tables(text: str) -> dict[str, list[list[str]]]:
    out = {}
    for m in TABLE.finditer(text):
        cols = []
        for line in m.group(2).splitlines():
            if line.startswith("  indexes"):
                break
            cm = COLUMN.match(line)
            if cm:
                cols.append([cm.group(1), cm.group(2)])
        out[m.group(1)] = cols
    return out


def build() -> dict:
    catalog: dict = {"tables": {}, "migrations": []}
    for layer, path in LAYERS:
        for name, cols in tables(path.read_text(encoding="utf-8")).items():
            catalog["tables"][name] = {"layer": layer, "source": path.relative_to(ROOT).as_posix(), "columns": cols,
                                       "fingerprint": fingerprint(cols)}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        catalog["migrations"].append({"filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    header = HEADER.search((ROOT / "docs" / "schema" / "rds.dbml").read_text(encoding="utf-8"))
    catalog["rds_version"] = header.group(1) if header else None
    return catalog


def render(catalog: dict) -> str:
    return json.dumps(catalog, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="파일이 최신인지 확인만")
    args = ap.parse_args(argv)
    text = render(build())
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT.relative_to(ROOT)} 가 DBML·마이그레이션과 다릅니다. "
                  "python scripts/schema/build_schema_catalog.py 로 다시 만드세요", file=sys.stderr)
            return 1
        print("스키마 카탈로그 최신")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"{OUT.relative_to(ROOT)} ({len(build()['tables'])}개 표)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
