"""운영 화면 탭2 가 비교·표시할 "문서상 스키마"를 만든다: src/rebom/serving/ops_schema_catalog.json (커밋한다).

API 이미지에는 docs/·db/ 가 없어서, DBML 의 표·칼럼과 마이그레이션 파일 sha256 을 이 JSON 으로 옮겨 API 가 읽는다.
화면은 이것을 실제 값(ops_schema_snapshot 의 Silver·Gold 칼럼, RDS information_schema·schema_migrations)과 비교한다.
흐름 ERD 용으로 표·칼럼 설명(note)과 예시('예:' 줄), 값 목록(Enum), 영역(TableGroup), 연결(Ref: links.dbml 처리 연결 +
rds.dbml 실제 외래키)도 함께 옮긴다. 지문(fingerprint)은 칼럼 이름·타입만으로 만든다(설명을 고쳐도 지문은 그대로).
DBML·마이그레이션을 고치면 다시 만든다. 안 만들면 tests/test_ops_api.py 와 CI(--check)가 실패한다.

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
LINKS = ROOT / "docs" / "schema" / "links.dbml"
MIGRATIONS = ROOT / "db" / "migrations"
Q3 = "'" * 3
TABLE = re.compile(r"^Table (\w+)([^{]*)\{\n(.*?)^\}", re.S | re.M)
COLUMN = re.compile(r"^  (\w+) (\S+)")
ENUM = re.compile(r"^Enum (\w+) \{\n(.*?)^\}", re.S | re.M)
ENUM_VALUE = re.compile(r"""^  "?([^"\s\[]+)"?(?: \[note: '(.*)'\])?\s*$""")
GROUP = re.compile(r"^TableGroup (\w+)(?: \[(.*?)\])? \{\n(.*?)^\}", re.S | re.M)
NOTE = re.compile(r"note: (?:'''(.*?)'''|'((?:[^'\\]|\\.)*)')", re.S)
COLOR = re.compile(r"color: (#[0-9A-Fa-f]{6})")
REF = re.compile(r"^Ref: (\w+)\.(\(?[\w, ]+\)?) ([<>-]) (\w+)\.(\(?[\w, ]+\)?)(?: \[(.*)\])?\s*$")
HEADER = re.compile(r"^// 버전: 스키마 (v\d+\.\d+) = 마이그레이션 (\d{3})~(\d{3})", re.M)


def fingerprint(columns: list[list[str]]) -> str:
    """rebom.common.ops_log.fingerprint 와 같은 방식 (칼럼 이름·타입, 순서 무관)."""
    pairs = sorted((str(n), str(t).lower()) for n, t in columns)
    return hashlib.sha256(json.dumps(pairs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _note(settings: str) -> tuple[str | None, str | None]:
    """설정 안의 note 를 (설명, 예시) 로 나눈다. 예시 = '예:' 로 시작하는 줄."""
    m = NOTE.search(settings or "")
    if not m:
        return None, None
    text = (m.group(1) if m.group(1) is not None else m.group(2).replace("\\'", "'")).strip()
    desc, example = [], None
    for line in text.splitlines():
        if line.startswith("예:"):
            example = line[2:].strip()
        else:
            desc.append(line)
    return "\n".join(desc).strip() or None, example


def _columns(body: str) -> list[dict]:
    """표 본문의 칼럼. 설명이 여러 줄(''' ''')이어도 한 칼럼으로 묶고, indexes 앞에서 멈춘다."""
    entries: list[list[str]] = []
    in_text = False
    for line in body.splitlines():
        if not in_text and line.startswith("  indexes"):
            break
        if not in_text and COLUMN.match(line):
            entries.append([line])
        elif entries:
            entries[-1].append(line)
        if line.count(Q3) % 2 == 1:
            in_text = not in_text
    cols = []
    for lines in entries:
        first = COLUMN.match(lines[0])
        rest = "\n".join(lines)[first.end():].strip()
        settings = rest[1:rest.rfind("]")] if rest.startswith("[") else ""
        head = NOTE.sub("", settings)
        desc, example = _note(settings)
        cols.append({"name": first.group(1), "type": first.group(2), "note": desc, "example": example,
                     "pk": bool(re.search(r"\bpk\b", head)), "notNull": "not null" in head})
    return cols


def table_docs(text: str) -> dict[str, dict]:
    return {m.group(1): {"note": _note(m.group(2))[0], "columns": _columns(m.group(3))} for m in TABLE.finditer(text)}


def tables(text: str) -> dict[str, list[list[str]]]:
    return {name: [[c["name"], c["type"]] for c in info["columns"]] for name, info in table_docs(text).items()}


def enums(text: str) -> dict[str, list[dict]]:
    out = {}
    for m in ENUM.finditer(text):
        values = []
        for line in m.group(2).splitlines():
            vm = ENUM_VALUE.match(line)
            if vm:
                values.append({"value": vm.group(1), "note": (vm.group(2) or "").replace("\\'", "'") or None})
        out[m.group(1)] = values
    return out


def groups(text: str, layer: str) -> dict[str, dict]:
    out = {}
    for m in GROUP.finditer(text):
        color = COLOR.search(m.group(2) or "")
        out[m.group(1)] = {"layer": layer, "color": color.group(1) if color else None,
                           "note": _note(m.group(2) or "")[0],
                           "tables": [t.strip() for t in m.group(3).splitlines() if t.strip()]}
    return out


def _cols(spec: str) -> list[str]:
    return [c.strip() for c in spec.strip("()").split(",") if c.strip()]


def refs(text: str, kind: str) -> list[dict]:
    """Ref 줄. 바로 위 '// 키 · 설명' 주석을 key·how 로 붙인다. from = 만든 쪽(뒤 단계), to = 읽은 쪽(앞 단계)."""
    out, comment = [], None
    for line in text.splitlines():
        if line.startswith("//"):
            comment = None if line.startswith("// ─") else line[2:].strip()
            continue
        m = REF.match(line)
        if m:
            a, ac, op, b, bc, settings = m.groups()
            if op == "<":
                a, ac, b, bc = b, bc, a, ac
            key, how = comment.split(" · ", 1) if comment and " · " in comment else (None, comment)
            key = key or "·".join(_cols(ac))  # 주석이 없으면 만든 쪽 칼럼 이름
            color = COLOR.search(settings or "")
            out.append({"from": {"table": a, "columns": _cols(ac)}, "to": {"table": b, "columns": _cols(bc)},
                        "kind": kind, "key": key, "how": how, "color": color.group(1) if color else None})
        else:
            comment = None
    return out


def build() -> dict:
    catalog: dict = {"tables": {}, "migrations": [], "enums": {}, "groups": {}, "refs": []}
    fks: list[dict] = []
    for layer, path in LAYERS:
        text = path.read_text(encoding="utf-8")
        catalog["enums"].update(enums(text))
        catalog["groups"].update(groups(text, layer))
        for name, info in table_docs(text).items():
            cols = [[c["name"], c["type"]] for c in info["columns"]]
            catalog["tables"][name] = {
                "layer": layer, "source": path.relative_to(ROOT).as_posix(), "columns": cols,
                "fingerprint": fingerprint(cols), "note": info["note"],
                "columnDocs": [{k: v for k, v in c.items() if k not in ("name", "type")} for c in info["columns"]]}
        if layer == "rds":
            fks = refs(text, "fk")
    catalog["refs"] = refs(LINKS.read_text(encoding="utf-8"), "link") + fks
    for name, group in catalog["groups"].items():
        for t in group["tables"]:
            if t in catalog["tables"]:
                catalog["tables"][t]["group"] = name
    for path in sorted(MIGRATIONS.glob("*.sql")):
        catalog["migrations"].append({"filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    header = HEADER.search(LAYERS[-1][1].read_text(encoding="utf-8"))
    catalog["rds_version"] = header.group(1) if header else None
    return catalog


def render(catalog: dict) -> str:
    return json.dumps(catalog, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="파일이 최신인지 확인만")
    args = ap.parse_args(argv)
    catalog = build()
    text = render(catalog)
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT.relative_to(ROOT)} 가 DBML·마이그레이션과 다릅니다. "
                  "python scripts/schema/build_schema_catalog.py 로 다시 만드세요", file=sys.stderr)
            return 1
        print("스키마 카탈로그 최신")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"{OUT.relative_to(ROOT)} ({len(catalog['tables'])}개 표, 연결 {len(catalog['refs'])}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
