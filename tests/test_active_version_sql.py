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
