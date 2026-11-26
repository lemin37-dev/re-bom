"""글 카드 템플릿 파일과 실행 버전 지문 (card_template.py, text_version.py)."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from rebom.embedding import card_template as ct, text_batch, text_card as tc, text_version as tv

TAGS = {"photo_trait": "photo_trait@1", "text_personality": "text-personality-v1", "text_health": "text-health-v1"}
# 파일로 나누기 전 코드로 만든 결과 (2026-11-24, develop 67fd866). 이 값이 바뀌면 글 벡터를 다시 만들어야 한다
KNOWN = {
    "A1": "믹스견, 2019년생, 수컷, 중성화함, 10kg 소형, 털색 흰색계열, (공고 털색 흰색). 특징: 경계 몸무게",
    "A2": "믹스견, 2026년생, 수컷, 중성화 안 함, 2.3kg 소형, 털색 흰색계열·검정계열·갈색계열, (공고 털색 갈색&검정&흰색), "
          "귀 늘어진 귀, 눈매 둥근 눈매, 털 복슬복슬, 성격 순함·친화적, 건강정보 있음. "
          "특징: 온순하고 사람을 좋아함. 전염병감염위험, 케어필요",
}
GOLDEN_SHA = {
    "A1": "ab2c77d5e599a623c35778d0398b2f62c87c1170227d819e6a0def68e71779d8",
    "A2": "a70046471aed6af6f7b0265ca35563f3f18fb1e93447308b468f949c7afaad0a",
}


# ---- 템플릿 파일 ------------------------------------------------------------------------------------------------------

def test_templates_are_locked_and_valid():
    """합쳐진 템플릿은 고치지 않는다. 파일을 고치거나 지문을 빠뜨리면 이 테스트가 막는다."""
    lock = json.loads((ct.TEMPLATE_DIR / "lock.json").read_text(encoding="utf-8"))
    assert set(lock) == set(ct.available()) and {"A1", "A2"} <= set(lock)
    for version in ct.available():
        assert ct.load(version).sha256 == lock[version]
    assert lock["A1"] == "473ebd58b3d8547461499326f537f206bf9698ae6c341b7e54a21d9012c4c51c"
    assert lock["A2"] == "c242b9e21ab3f64be4ae85c1abb63da30c534d8e814d35c71cf15acd0e67771e"


@pytest.mark.parametrize("version", ["A1", "A2"])
def test_card_text_does_not_change(version):
    cards = tv.golden_cards(version)
    assert KNOWN[version] in cards and len(cards) == 7  # 종료 공고는 빠짐
    assert ct.content_sha256(cards) == GOLDEN_SHA[version]


@pytest.mark.parametrize("version", ct.available())
def test_personal_information_never_appears(version):
    text = "\n".join(tv.golden_cards(version))
    assert "010-1234-5678" not in text and "02-123-4567" not in text and "12-345" not in text and "[번호]" in text
    card = tc.build_card({"specialMark": "관리번호 12-345 순함 010-1234-5678"}, version)
    assert "010" not in card and "12-345" not in card


def test_templates_are_shipped_in_package():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]
    assert "card_templates/*.json" in data["rebom.embedding"]


@pytest.fixture
def folder(tmp_path):
    target = tmp_path / "card_templates"
    shutil.copytree(ct.TEMPLATE_DIR, target)
    return target


def write(folder: Path, version: str, change=None, lock=True, base="A2"):
    content = json.loads((folder / f"{base}.json").read_text(encoding="utf-8"))
    content["template_version"] = version
    if change:
        change(content)
    (folder / f"{version}.json").write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    if lock:
        path = folder / "lock.json"
        path.write_text(json.dumps(json.loads(path.read_text()) | {version: ct.content_sha256(content)}), encoding="utf-8")
    return content


def test_new_template_needs_only_a_file_and_a_lock(folder):
    """A3: 문구·순서·항목은 파일만으로 바꾼다 (코드 수정 없음)."""
    def change(c):
        c["fields"] = [{"value": "tags"}, {"value": "breed", "text": "품종 {}"}, {"value": "sex", "map": {"M": "남아", "F": "여아"}}]
        c["tail"] = {"value": "note", "text": " / {}"}

    write(folder, "A3", change)
    assert ct.available(folder) == ("A1", "A2", "A3")
    row = {"kindNm": "믹스견", "sexCd": "F", "weight": "3(Kg)", "specialMark": "순함 010-1234-5678"}
    assert ct.render(ct.load_from(folder, "A3"), row, [("털", "복슬복슬")]) == "털 복슬복슬, 품종 믹스견, 여아 / 순함 [번호]"
    a2 = ct.render(ct.load_from(folder, "A2"), row, [("털", "복슬복슬")])
    assert a2 == "믹스견, 암컷, 3kg 소형, 털 복슬복슬. 특징: 순함 [번호]"  # 기존 템플릿은 그대로


def test_editing_existing_template_is_rejected(folder):
    write(folder, "A2", lambda c: c["tail"].update(text=". 특이사항: {}"), lock=False)
    with pytest.raises(ct.TemplateError, match="등록된 지문과 다름"):
        ct.load_from(folder, "A2")


def test_whitespace_and_line_endings_do_not_matter(folder):
    path = folder / "A2.json"
    path.write_bytes(path.read_text(encoding="utf-8").replace("\n", "\r\n").replace("  ", "    ").encode("utf-8"))
    assert ct.load_from(folder, "A2").sha256 == ct.load("A2").sha256


def test_template_without_lock_or_unknown_is_rejected(folder):
    write(folder, "A3", lock=False)
    with pytest.raises(ct.TemplateError, match="lock.json 에 지문이 없음"):
        ct.load_from(folder, "A3")
    with pytest.raises(ct.TemplateError, match="Unsupported"):
        ct.load_from(folder, "B1")


@pytest.mark.parametrize("change, message", [
    (lambda c: c["fields"].append({"value": "region"}), "없는 값 'region'"),
    (lambda c: c["fields"].append({"value": "breed"}), "겹치는 값 breed"),
    (lambda c: c["fields"][1].update(text="년생"), "{} 가 하나"),
    (lambda c: c["fields"][1].update(text="{} {}"), "{} 가 하나"),
    (lambda c: c["fields"][2].update(map={}), "map 은"),
    (lambda c: c["fields"][0].update(code="__import__('os')"), "모르는 키 code"),
    (lambda c: c["fields"][-1].update(text="태그 {}"), "text·map 을 쓸 수 없음"),
    (lambda c: c.update(fields=[]), "fields 는"),
    (lambda c: c.update(separator=""), "separator 는"),
    (lambda c: c.update(tail={"value": "tags"}), "tail 에는"),
    (lambda c: c["params"].update(note_source="care_tel"), "note_source 는"),
    (lambda c: c["params"].update(allow_soci="yes"), "allow_soci 는 bool"),
    (lambda c: c["params"].pop("allow_soci"), "params 는"),
    (lambda c: c.update(mask=False), "모르는 키 mask"),  # 가리기를 템플릿에서 끌 수 없다
    (lambda c: c.pop("tail"), "빠진 키 tail"),
    (lambda c: c.update(template_version="A9"), "파일 이름"),
])
def test_bad_template_is_rejected_before_use(folder, change, message):
    write(folder, "A3", change)
    with pytest.raises(ct.TemplateError) as error:
        ct.load_from(folder, "A3")
    assert message in str(error.value)


# ---- 실행 버전 지문 ----------------------------------------------------------------------------------------------------

def test_runtime_config_records_content_and_fingerprints():
    config = tv.runtime_config("A2", TAGS)
    assert config["template"] == json.loads((ct.TEMPLATE_DIR / "A2.json").read_text(encoding="utf-8"))  # 내용 전체
    assert config["template_sha256"] == ct.load("A2").sha256 and config["golden_sha256"] == GOLDEN_SHA["A2"]
    assert config["tag_versions"] == TAGS and config["doc_prefix"] == "title: none | text: "
    tv.check_config(config)
    assert tv.has_fingerprint(config) and not tv.has_fingerprint({"template_version": "A2"})


@pytest.mark.parametrize("change, key", [
    (lambda c: c["template"]["tail"].update(text=". 특이사항: {}"), "template"),
    (lambda c: c.update(golden_sha256="0" * 64), "golden_sha256"),
    (lambda c: c.update(doc_prefix="title: pet | text: "), "doc_prefix"),
    (lambda c: c.update(dim=3072), "dim"),
])
def test_difference_from_registered_config_stops(change, key):
    config = copy.deepcopy(tv.runtime_config("A2", TAGS))
    change(config)
    with pytest.raises(ct.TemplateError, match=key):
        tv.check_config(config)


@pytest.mark.parametrize("target, name, value", [
    (tc, "size_label", lambda k, inclusive: "작음"),
    (tc, "PHONE", __import__("re").compile("없는 번호")),
    (tc, "FAMILY", {"흰색계열": ["흰"]}),
    (text_batch, "PERSONALITY", {"gentle": "온순"}),
    (text_batch, "PHOTO_LABELS", {"fur": "털결", "ear": "귀", "eye": "눈매"}),
])
def test_code_change_changes_golden_fingerprint(monkeypatch, target, name, value):
    """템플릿 파일은 그대로인데 계산·가리기·이름표 코드가 바뀐 경우도 잡는다."""
    config = tv.runtime_config("A2", TAGS)
    monkeypatch.setattr(target, name, value)
    with pytest.raises(ct.TemplateError, match="golden_sha256"):
        tv.check_config(config)


@pytest.fixture
def registry(tmp_path):
    pytest.importorskip("pyiceberg")
    pytest.importorskip("sqlalchemy")
    from pyiceberg.catalog.sql import SqlCatalog

    from rebom.common import gold_catalog as gc

    catalog = SqlCatalog("t", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    env = gc.GoldEnvironment("test", "rebom_gold", "_test", f"file://{tmp_path}/wh/gold")
    gc.create_tables(env, catalog, group="registry")
    return catalog, env


def test_register_keeps_history_with_template_content(registry):
    from rebom.common.gold_version_registry import register_version

    catalog, env = registry
    old = {"model_id": "gemini-embedding-2", "dim": 768, "template_version": "A2", "tag_versions": {}}
    register_version(catalog, env, tv.TASK, "text_embedding@1", old)  # 지문이 없던 시험 등록
    config = tv.runtime_config("A2", TAGS)
    assert tv.register(catalog, env, "text_embedding@2", config)
    assert not tv.register(catalog, env, "text_embedding@2", config)  # 같은 설정이면 그대로
    newer = tv.runtime_config("A2", TAGS | {"photo_trait": "photo_trait@2"})  # 태그 버전만 바뀜: 템플릿은 같고 실행 버전만 오름
    assert tv.register(catalog, env, "text_embedding@10", newer)
    history = tv.registered(catalog, env)
    assert [r["version"] for r in history] == ["text_embedding@1", "text_embedding@2", "text_embedding@10"]
    assert history[1]["config"]["template"]["tail"]["text"] == ". 특징: {}"  # 내용이 함께 남음
    with pytest.raises(ct.TemplateError, match="지문이 없음"):
        tv.register(catalog, env, "text_embedding@11", old)


def test_same_template_name_with_other_content_is_rejected(registry):
    from rebom.common.gold_version_registry import register_version

    catalog, env = registry
    other = copy.deepcopy(tv.runtime_config("A2", TAGS))
    other["template_sha256"] = "f" * 64
    register_version(catalog, env, tv.TASK, "text_embedding@2", other)  # 다른 코드에서 다른 A2 로 등록한 경우
    config = tv.runtime_config("A2", TAGS)
    with pytest.raises(ct.TemplateError, match="text_embedding@2"):
        tv.register(catalog, env, "text_embedding@3", config)
    with pytest.raises(ct.TemplateError, match="text_embedding@2"):
        tv.verify(catalog, env, config, write=False)


def test_version_without_fingerprint_can_plan_but_not_write(registry):
    catalog, env = registry
    old = {"model_id": "gemini-embedding-2", "dim": 768, "template_version": "A2", "tag_versions": TAGS}
    assert tv.verify(catalog, env, old, write=False) is False
    with pytest.raises(ct.TemplateError, match="쓸 수 없음"):
        tv.verify(catalog, env, old, write=True)


def test_cli_config_register_history(registry, monkeypatch, tmp_path, capsys):
    from rebom.common import gold_catalog as gc

    catalog, env = registry
    monkeypatch.setattr("rebom.storage.silver.aws_catalog.get_aws_catalog", lambda **k: catalog)
    monkeypatch.setattr(gc, "get_gold_environment", lambda name: env)
    path = tmp_path / "c.json"
    assert tv.main(["config", "--template", "A2", "--out", str(path), *(x for t, v in TAGS.items() for x in ("--tag", f"{t}={v}"))]) == 0
    assert json.loads(path.read_text(encoding="utf-8")) == tv.runtime_config("A2", TAGS)
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())  # PowerShell 이 붙이는 BOM 도 읽는다
    assert tv.main(["register", "--version", "text_embedding@2", "--config", str(path)]) == 0
    capsys.readouterr()
    assert tv.main(["history"]) == 0
    row = json.loads(capsys.readouterr().out)
    assert (row["version"], row["template_version"], row["template_sha256"]) == ("text_embedding@2", "A2", ct.load("A2").sha256)
    path.write_text("{\"template_version\": \"A2\"}", encoding="utf-8")
    assert tv.main(["register", "--version", "text_embedding@3", "--config", str(path)]) == 2
    with pytest.raises(ValueError, match="--tag"):
        tv.main(["config", "--template", "A2", "--tag", "photo_color=1"])
