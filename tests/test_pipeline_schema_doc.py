"""층별 DBML(docs/schema/*.dbml, 설명 원본)과 실제 구조 원본(코드)의 표·칼럼 이름이 같은지 본다.

RDS 는 tests/test_rds_schema_doc.py 가 마이그레이션과 비교한다. 이 파일은 그 밖의 층을 맡는다.
  Bronze  수집 manifest 키(rescue_incremental), 정제가 읽는 API 필드(transform_notice·transform_shelter),
          사진 현재 스냅샷 칸(photo_snapshot), 사진 manifest 키(rescue_photos), 수집 상태 키
  Silver  표 생성 스크립트(scripts/storage/rebom_create_aws_silver_tables.py · rebom_create_silver_tables.py), 발행 기록(publish_log)
  Gold    src/rebom/common/gold_catalog.py 의 schemas()
  (운영 기록 ops_* 는 RDS 표라 docs/rds_schema.dbml 과 마이그레이션 008 에 있고 test_rds_schema_doc.py 가 본다)
코드는 import 하지 않고 ast 로 읽는다(pyiceberg·boto3 없이 돈다). 한쪽만 고치면 실패한다.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "docs" / "schema"
LAYER_FILES = {name: SCHEMA / f"{name}.dbml" for name in ("bronze", "silver", "gold")}
RDS = ROOT / "docs" / "rds_schema.dbml"
LINKS = SCHEMA / "links.dbml"

# 구조 원본이 아직 없어 이름 비교를 하지 않는 표 (원본이 생기면 지우고 비교에 넣는다)
UNCHECKED = {
    "bronze_photo_state": "사진 기록 JSON 은 여러 dict 로 나눠 만들어 한 곳의 키 목록이 없음",
}


# ───────── DBML 읽기 ─────────

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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


def dbml_names(text: str, kind: str) -> list[str]:
    return re.findall(rf"^{kind} (\w+)", text, re.M)


def table_groups(text: str) -> dict[str, list[str]]:
    groups = {}
    for m in re.finditer(r"^TableGroup (\w+)[^{]*\{\n(.*?)^\}", text, re.S | re.M):
        groups[m.group(1)] = re.findall(r"^\s+(\w+)\s*$", m.group(2), re.M)
    return groups


def layer(name: str) -> dict[str, list[str]]:
    return dbml_tables(_read(LAYER_FILES[name]))


# ───────── 코드 읽기 (ast) ─────────

def _module(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8-sig"))


def _constants(tree: ast.Module) -> dict[str, ast.expr]:
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = node.value
    return out


def _strings(node: ast.expr, consts: dict[str, ast.expr]) -> list[str]:
    """튜플·리스트 안의 문자열. *이름 은 같은 모듈 상수를 펼친다."""
    out = []
    for elt in node.elts:
        if isinstance(elt, ast.Starred) and isinstance(elt.value, ast.Name):
            out += _strings(consts[elt.value.id], consts)
        elif isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            out.append(elt.value)
    return out


def _field_list(tree: ast.Module, var: str) -> list[str]:
    """[("칸", 타입), …] 목록의 칸 이름."""
    node = _constants(tree)[var]
    return [e.elts[0].value for e in node.elts if isinstance(e, ast.Tuple)]


def _dict_keys(node: ast.AST) -> set[str]:
    keys = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Dict):
            keys |= {k.value for k in sub.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return keys


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def silver_sources() -> dict[str, list[str]]:
    aws = _module("scripts/storage/rebom_create_aws_silver_tables.py")
    local = _module("scripts/storage/rebom_create_silver_tables.py")
    publish = _module("src/rebom/storage/silver/publish_log.py")
    ret = [n for n in ast.walk(_function(publish, "build_publish_record")) if isinstance(n, ast.Return)][-1]
    return {
        "silver_animal_notice": _field_list(aws, "notice_fields"),
        "silver_animal_status_history": _field_list(aws, "history_fields"),
        "silver_animal_image": _field_list(local, "image_fields"),
        "silver_animal_image_version_history": _field_list(local, "image_version_history_fields"),
        "silver_shelter": _field_list(local, "shelter_fields"),
        # publish_seq 는 발행 뒤 reserve_publish_seq 가, published_at 은 save_publish_record_s3 가 채운다
        "silver_publish": sorted(_dict_keys(ret.value) | {"publish_seq", "published_at"}),
    }


def gold_sources() -> dict[str, list[str]]:
    tree = _module("src/rebom/common/gold_catalog.py")
    consts = {k: v.value for k, v in _constants(tree).items() if isinstance(v, ast.Constant)}
    fn = _function(tree, "schemas")

    def columns(call: ast.Call) -> list[str]:
        cols = []
        for arg in call.args:
            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
                if arg.func.id == "f":
                    cols.append(arg.args[1].value)
                elif arg.func.id == "vector":
                    cols.append("embedding")
        return cols

    named = {}
    for node in fn.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == "Schema"):
            named[node.targets[0].id] = columns(node.value)
    ret = next(n for n in fn.body if isinstance(n, ast.Return))
    out = {}
    for key, value in zip(ret.value.keys, ret.value.values):
        schema = value.elts[0]
        out[consts[key.id]] = named[schema.id] if isinstance(schema, ast.Name) else columns(schema)
    return out


def bronze_sources() -> dict[str, list[str]]:
    inc = _module("src/rebom/ingestion/rescue_incremental.py")
    main = _function(inc, "main")
    manifest = next(n.value for n in ast.walk(main) if isinstance(n, ast.Assign)
                    and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "manifest")
    later = {n.targets[0].slice.value for n in ast.walk(main) if isinstance(n, ast.Assign)
             and isinstance(n.targets[0], ast.Subscript) and isinstance(n.targets[0].value, ast.Name)
             and n.targets[0].value.id == "manifest"}
    save_state = next(n for n in ast.walk(main) if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Name) and n.func.id == "save_state")

    notice = "\n".join(p.read_text(encoding="utf-8-sig") for p in (
        ROOT / "src/rebom/storage/silver/transform_notice.py", *sorted((ROOT / "src/rebom/storage/cleaning").glob("*.py"))))
    shelter = _module("src/rebom/storage/silver/transform_shelter.py")
    shelter_keys = {k.value for k in _constants(shelter)["SHELTER_FIELDS"].keys}
    record = set(re.findall(r'raw\.get\("(\w+)"\)', notice)) | shelter_keys | {"careRegNo"}

    snap = _module("src/rebom/storage/silver/photo_snapshot.py")
    consts = _constants(snap)
    parse_row = _function(snap, "_parse_row")
    required = next(n.value for n in ast.walk(parse_row) if isinstance(n, ast.Assign)
                    and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "required")
    current = {"desertion_no", "slot", "slot_state"} | set(_strings(required, consts)) | set(_strings(consts["PHOTO_FIELDS"], consts))

    photos = _module("src/rebom/ingestion/rescue_photos.py")
    image_manifest = next(_dict_keys(d) for d in ast.walk(photos) if isinstance(d, ast.Dict)
                          and {"counts", "failed", "files"} <= _dict_keys(d))
    image_manifest.discard("stored")  # counts 안의 키라 칸이 아님
    return {
        "rescue_state": sorted(_dict_keys(save_state)),
        "bronze_rescue_manifest": sorted(_dict_keys(manifest) | later),
        "bronze_rescue_record": sorted(record),
        "bronze_image_current": sorted(current),
        "bronze_image_manifest": sorted(image_manifest),
    }


# ───────── 이름 비교 ─────────

def _compare(doc: dict[str, list[str]], src: dict[str, list[str]], *, ignore: set[str] = frozenset()):
    assert set(doc) - set(UNCHECKED) == set(src), (
        f"표 목록이 다름: 문서만 {set(doc) - set(src) - set(UNCHECKED)}, 코드만 {set(src) - set(doc)}")
    for name, cols in src.items():
        have, want = set(doc[name]) - ignore, set(cols)
        assert have == want, f"{name}: 문서만 {sorted(have - want)}, 코드만 {sorted(want - have)}"


def test_parsers_find_tables():
    counts = {name: len(layer(name)) for name in LAYER_FILES}
    assert counts == {"bronze": 6, "silver": 6, "gold": 8}
    assert len(gold_sources()) == 8
    assert "desertion_no" in silver_sources()["silver_animal_notice"]


def test_silver_matches_create_scripts():
    _compare(layer("silver"), silver_sources())


def test_gold_matches_catalog():
    _compare(layer("gold"), gold_sources())


def test_bronze_matches_ingestion_code():
    doc = layer("bronze")
    # popfile1 은 popfile1~8 여덟 칸을 대표로 적은 것. 정제는 사진 칸을 읽지 않는다
    _compare(doc, bronze_sources(), ignore={"popfile1"})


def test_bronze_has_no_owner_fields():
    forbidden = set(_strings(ast.parse("x = " + re.search(
        r"FORBIDDEN_FIELDS = (\{.*?\})", (ROOT / "src/rebom/storage/silver/load_bronze.py").read_text(encoding="utf-8-sig")
    ).group(1).replace("{", "(").replace("}", ",)")).body[0].value, {}))
    assert "careOwnerNm" in forbidden
    for name in LAYER_FILES:
        for table, cols in layer(name).items():
            assert not forbidden & set(cols), f"{table}: 보호자 정보 칸 {forbidden & set(cols)}"


# ───────── 문서 규칙 ─────────

def test_every_column_has_note_and_example():
    for name, path in LAYER_FILES.items():
        text = _read(path)
        for m in re.finditer(r"^Table (\w+)([^{]*)\{\n(.*?)^\}", text, re.S | re.M):
            assert "note:" in m.group(2), f"{name}.{m.group(1)}: 표 note 없음"
            body = m.group(3)
            for cm in re.finditer(r"^  (\w+) (\S+) \[(.*?)'''\]$", body, re.S | re.M):
                if cm.group(1) == "indexes":
                    continue
                assert "note:" in cm.group(3), f"{m.group(1)}.{cm.group(1)}: 설명 없음"
                assert "\n예: " in cm.group(3), f"{m.group(1)}.{cm.group(1)}: '예:' 줄 없음"
            columns = [ln for ln in body.splitlines() if re.match(r"  \w+ \S", ln) and not ln.startswith("  indexes")]
            assert all("note:" in ln for ln in columns), f"{m.group(1)}: 한 줄짜리 칸에 note 없음"


def test_every_table_is_in_its_layer_group():
    for name, path in LAYER_FILES.items():
        text = _read(path)
        grouped = {t for ts in table_groups(text).values() for t in ts}
        assert set(dbml_tables(text)) == grouped, f"{name}: TableGroup 에 빠진 표 {set(dbml_tables(text)) - grouped}"


def test_names_unique_across_files():
    texts = [_read(p) for p in (*LAYER_FILES.values(), RDS)]
    for kind in ("Table", "Enum", "TableGroup"):
        names = [n for t in texts for n in dbml_names(t, kind)]
        dup = {n for n in names if names.count(n) > 1}
        assert not dup, f"{kind} 이름이 파일 사이에서 겹침: {dup}"


def test_links_point_to_existing_columns():
    tables = {}
    for p in (*LAYER_FILES.values(), RDS):
        tables.update(dbml_tables(_read(p)))
    text = _read(LINKS)
    assert not dbml_names(text, "Table"), "links.dbml 에는 Ref 만 둔다"
    refs = re.findall(r"^Ref\s*\w*\s*:\s*(.+?)\s*[<>-]\s*(.+?)(?:\s*\[.*\])?$", text, re.M)
    assert len(refs) >= 40
    for ends in refs:
        for end in ends:
            m = re.fullmatch(r"(\w+)\.(?:\((.+)\)|(\w+))", end)
            assert m, f"links.dbml: 읽을 수 없는 끝 {end}"
            table, cols = m.group(1), [c.strip() for c in (m.group(2) or m.group(3)).split(",")]
            assert table in tables, f"links.dbml: 없는 표 {table}"
            for col in cols:
                assert col in tables[table], f"links.dbml: {table} 에 없는 칼럼 {col}"
    colors = set(re.findall(r"color: (#[0-9A-Fa-f]{6})", text))
    assert colors <= {"#2459D6", "#D9730D", "#7A3FC2", "#0F8B8D", "#C2185B", "#6B7A10", "#8A5A2B"}, colors


def test_merge_script_contains_every_table(tmp_path):
    out = tmp_path / "merged.dbml"
    subprocess.run([sys.executable, str(ROOT / "scripts/schema/merge_pipeline_dbml.py"), "--out", str(out)],
                   check=True, capture_output=True)
    merged = out.read_text(encoding="utf-8")
    expected = set()
    for p in (*LAYER_FILES.values(), RDS):
        expected |= set(dbml_tables(_read(p)))
    assert set(dbml_tables(merged)) == expected
    assert merged.rstrip().endswith(_read(LINKS).rstrip().splitlines()[-1])


def test_files_are_lf_utf8():
    for path in (*LAYER_FILES.values(), LINKS, ROOT / "scripts/schema/merge_pipeline_dbml.py"):
        raw = path.read_bytes()
        raw.decode("utf-8")
        assert b"\r\n" not in raw, f"{path.name}: CRLF"
