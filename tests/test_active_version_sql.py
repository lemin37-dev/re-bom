import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "active_version_sql", Path(__file__).resolve().parents[1] / "scripts" / "serve" / "active_version_sql.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_insert_sql_escapes_and_keeps_previous():
    sql = mod.insert_sql("image_embedding", "image_embedding@1", "a" * 64,
                         {"model_id": "gemini-embedding-2", "dim": 768, "preprocess_version": "p1"}, "o'neil")
    assert "'o''neil'" in sql
    assert '\'{"dim": 768, "model_id": "gemini-embedding-2"}\'::jsonb' in sql
    assert "ON CONFLICT (task)" in sql and "previous_version" in sql


def test_query_config_none_without_model_keys():
    assert mod.query_config({"prompt_version": "v9"}) is None
    assert "NULL, 'lemin'" in mod.insert_sql("photo_trait", "photo_trait@1", "b" * 64, {"prompt_version": "v9"}, "lemin")


def _load(name):
    s = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "scripts" / "serve" / f"{name}.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def test_legacy_configs_match_what_sync_reads():
    from rebom.serving.sync.inputs import legacy_key
    from rebom.tagging import text_tags
    from rebom.tagging.jev_client import MODEL

    leg = _load("legacy_version_config")
    img = leg.config_for("image_embedding")
    assert legacy_key("image_embedding", img) == ("gemini-embedding-2:768", "exif-rgb-max1024-jpeg90")
    for task in ("text_personality", "text_health"):
        assert legacy_key(task, leg.config_for(task)) == (MODEL, text_tags.VERSIONS[task])


def test_compare_legacy_tags_same_and_diff():
    cmp = _load("compare_legacy_tags")

    def g(sha, code, version, task="photo_trait"):
        return {"subject_type": "photo", "subject_key": sha, "task": task, "version": version, "tag_code": code,
                "rank": None, "similarity": None, "searchable": True, "displayable": True}

    rows = [g("a", "fur.fluffy", "photo_trait@1"), g("a", "fur.fluffy", None),           # 같음
            g("b", "fur.short", "photo_trait@1"), g("b", "fur.fluffy", None),            # 다름
            g("c", "ear.drop", None)]                                                    # 옛 행만: 세지 않음
    out, diffs = cmp.compare(rows)
    assert out["photo_trait"] == {"rows_versioned": 2, "rows_legacy": 3, "subjects_legacy_only": 1,
                                  "same": 1, "diff": 1, "legacy_rows_same": 1, "legacy_rows_diff": 1}
    assert [d[0][1] for d in diffs] == ["b"]


def test_fill_legacy_only_where_no_versioned_rows():
    mod = _load("fill_legacy_photo_tag_versions")
    legacy = [{"subject_key": "a", "tag_code": "fur.fluffy", "version": None},
              {"subject_key": "b", "tag_code": "ear.drop", "version": None},
              {"subject_key": "b", "tag_code": "eye.round", "version": None}]
    fill, dropped = mod.split_legacy(legacy, {"a"}, "photo_trait@1")
    assert dropped == 1
    assert [(r["subject_key"], r["version"]) for r in fill] == [("b", "photo_trait@1"), ("b", "photo_trait@1")]
    assert legacy[1]["version"] is None  # 원본은 그대로


def test_text_fill_detects_overlap():
    mod = _load("fill_legacy_text_tag_versions")
    keys = ("subject_type", "subject_key", "task", "version", "input_hash")
    old = [{"subject_type": "animal", "subject_key": "1", "task": "text_health", "version": "text-health-v1",
            "input_hash": "h"}]
    filled = mod.with_version(old, "text_health@1")
    assert filled[0]["version"] == "text_health@1" and old[0]["version"] == "text-health-v1"
    assert mod.overlaps(filled, [], keys) == 0
    assert mod.overlaps(filled, [dict(filled[0])], keys) == 1
