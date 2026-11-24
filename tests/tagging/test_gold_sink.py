"""Gold 쓰기: 로컬 SQLite 카탈로그 + 임시 폴더로 Iceberg 동작을 확인한다(AWS 없음).

- 사진 태그의 대상은 사진 sha256 (subject_type=photo). 다시 태깅하면 (사진, 작업, 질문지 버전) 범위만 교체하고,
  다른 사진·다른 작업·이전 질문지 버전 태그는 그대로 둔다.
- 처리 기록은 (subject_type, subject_key, task, version, input_hash) 로 upsert 된다.
- 키 칸이 없는 옛 테이블은 칸 추가로 맞추지 않고 멈추며, test 에서만 --recreate 로 다시 만든다.
"""
import json
from datetime import UTC, datetime

import pytest

pytest.importorskip("pyiceberg")
pytest.importorskip("sqlalchemy")

from rebom.common import gold_catalog as gc
from rebom.tagging import run_photo_tags as rpt, version_config


@pytest.fixture()
def sink(tmp_path):
    from pyiceberg.catalog.sql import SqlCatalog

    catalog = SqlCatalog("t", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    env = gc.GoldEnvironment("unit", "rebom_gold", "_unit", f"file://{tmp_path}/wh/gold")
    made = gc.create_tables(env, catalog)
    assert len(made) == len(gc.schemas())  # 태깅·임베딩 묶음 전부
    assert gc.create_tables(env, catalog) == []  # 두 번째는 만들 것이 없다
    return rpt.GoldSink(catalog, env)


def tag(sha, code, task, ts, version="v"):
    return {
        "subject_type": "photo", "subject_key": sha, "tag_code": code, "source": "photo", "tagger_version": f"m@{task}",
        "task": task, "model": "m", "prompt_version": version, "source_hash": sha, "searchable": True,
        "displayable": True, "rank": None, "similarity": None, "detail": "[]", "gold_run_id": "r", "gold_updated_at": ts,
    }


def log(sha, task, status, attempts, ts, run="r"):
    return {"subject_type": "photo", "subject_key": sha, "task": task, "version": "v", "input_hash": sha, "status": status,
            "attempts": attempts, "tag_count": 0, "last_error": None, "gold_run_id": run, "gold_updated_at": ts}


def test_overwrite_scope_and_upsert(sink):
    ts = datetime.now(UTC)
    t, b = rpt.trait_tags.TASK, rpt.breed_tags.TASK
    sink.write(
        [("h1", t, "v"), ("h1", b, "v"), ("h2", t, "v")],
        [tag("h1", "ear.prick", t, ts), tag("h1", "fur.short", t, ts), tag("h1", "breed.진도견", b, ts),
         tag("h2", "ear.drop", t, ts)],
        [log("h1", t, "FAILED", 1, ts)],
        [],
    )
    # 사진 h1 특징만 다시 태깅: 태그 없음(NO_RESULT)
    sink.write(
        [("h1", t, "v")], [],
        [log("h1", t, "NO_RESULT", 2, ts, run="r2")],
        [{"request_id": "q", "subject_type": "photo", "subject_key": "h1", "task": t, "prompt_version": "v", "run_no": 1,
          "requested_model": "m", "response_model": "m2", "provider": "p", "canonical_slug": "c",
          "media_resolution": "MEDIA_RESOLUTION_MEDIUM", "prompt_tokens": 1294, "completion_tokens": 1, "reasoning_tokens": 0,
          "cost_usd": 0.0007, "latency_ms": 1, "attempts": 1, "status": "OK", "error_code": None, "raw_answer": "{}",
          "requested_at": ts, "gold_run_id": "r2", "dt": "2026-11-20"}],
    )
    tags = {(r["subject_key"], r["tag_code"]) for r in sink.tag.scan().to_arrow().to_pylist()}
    assert tags == {("h1", "breed.진도견"), ("h2", "ear.drop")}
    logs = sink.read_inference_log()
    assert len(logs) == 1 and logs[0]["status"] == "NO_RESULT" and logs[0]["attempts"] == 2
    assert sink.requests.scan().to_arrow().to_pylist()[0]["subject_key"] == "h1"


def test_new_photo_or_version_keeps_previous_rows(sink):
    """새 사진은 새 sha256 행이 되고, 질문지 버전을 올려도 이전 행은 남는다(2026-11-20 결정)."""
    ts = datetime.now(UTC)
    t = rpt.trait_tags.TASK
    sink.write([("h1", t, "v")], [tag("h1", "ear.prick", t, ts)], [], [])
    sink.write([("h2", t, "v")], [tag("h2", "ear.drop", t, ts)], [], [])  # 공고 사진이 h1 → h2 로 바뀜
    sink.write([("h1", t, "v2")], [tag("h1", "ear.semi", t, ts, version="v2")], [], [])
    sink.write([("h2", t, "v")], [tag("h2", "fur.short", t, ts)], [], [])  # 같은 사진·같은 버전만 교체
    rows = {(r["subject_key"], r["prompt_version"], r["tag_code"]) for r in sink.tag.scan().to_arrow().to_pylist()}
    assert rows == {("h1", "v", "ear.prick"), ("h1", "v2", "ear.semi"), ("h2", "v", "fur.short")}


def test_registered_tag_versions_and_legacy_replacement_are_isolated(sink):
    ts = datetime.now(UTC)
    task = rpt.trait_tags.TASK
    v1, v2 = f"{task}@1", f"{task}@2"
    sink.write([("h1", task, "v")], [tag("h1", "ear.prick", task, ts)], [], [])

    sink.registered = True
    for version in (v1, v2):
        sink.write(
            [("h1", task, version)],
            [tag("h1", "ear.semi", task, ts) | {"version": version}],
            [log("h1", task, "OK", 1, ts) | {"version": version}],
            [{"task": task, "version": version, "requested_at": ts, "dt": ts.date().isoformat()}],
        )
    sink.write([("h1", task, v2)], [], [log("h1", task, "NO_RESULT", 2, ts) | {"version": v2}], [])
    sink.registered = False
    sink.write([("h1", task, "v")], [tag("h1", "ear.drop", task, ts)], [], [])

    assert {(row["version"], row["tag_code"]) for row in sink.tag.scan().to_arrow().to_pylist()} == {
        (None, "ear.drop"), (v1, "ear.semi"),
    }
    assert {row["version"]: row["status"] for row in sink.read_inference_log()} == {v1: "OK", v2: "NO_RESULT"}
    assert {row["version"] for row in sink.requests.scan().to_arrow().to_pylist()} == {v1, v2}


def test_select_and_run_pin_registered_tag_version(sink, monkeypatch):
    from types import SimpleNamespace

    from rebom.common.gold_version_registry import register_version

    catalog = sink.tag.catalog
    env = gc.GoldEnvironment("unit", "rebom_gold", "_unit", "")
    task = rpt.trait_tags.TASK
    version = f"{task}@1"
    assert register_version(catalog, env, task, version, version_config.runtime_config(task))
    sha = "a" * 64
    image = {"desertion_no": "1", "image_slot": 1, "sha256": sha, "original_s3_key": "pic"}
    target_file = {}
    monkeypatch.setattr(gc, "get_gold_environment", lambda name: env)
    monkeypatch.setattr(rpt, "_clients", lambda args: (catalog, object()))
    monkeypatch.setattr(rpt, "read_silver", lambda *args: (["1"], [image]))
    monkeypatch.setattr(rpt, "_write_json", lambda path, value, s3: target_file.update(value))
    monkeypatch.setattr(rpt, "_read_json", lambda *args: target_file)
    monkeypatch.setattr(rpt, "fetch_image", lambda *args: b"sample")
    monkeypatch.setattr(rpt, "OpenRouterClient", lambda key, *, app_name, app_url: SimpleNamespace(canonical_slug=lambda model: None))

    def tag_once(items, client, config):
        [(target, _image)] = items
        assert target.tasks == (task,)
        ts = datetime.now(UTC)
        outcome = rpt.TaskOutcome(task, rpt.OK, [tag(sha, "ear.prick", task, ts, rpt.VERSIONS[task])])
        request = {"task": task, "requested_at": ts, "dt": ts.date().isoformat(), "cost_usd": 0}
        return [rpt.PhotoOutcome(target, {task: outcome}, [request])], None

    monkeypatch.setattr(rpt, "tag_photos", tag_once)
    args = ["--targets", "target.json", "--run-id", "test"]
    assert rpt.main(["select", *args, "--version", version]) == 0
    assert target_file["versions"] == {task: version}
    with pytest.raises(ValueError, match="must match the target list"):
        rpt.main(["run", *args, "--version", f"{task}@2"])
    assert rpt.main(["run", *args]) == 0
    assert rpt.main(["run", *args]) == 0
    for base in (gc.TAG_TABLE, gc.INFERENCE_LOG_TABLE, gc.REQUEST_LOG_TABLE):
        [row] = catalog.load_table(env.table(base)).scan().to_arrow().to_pylist()
        assert row["version"] == version


def test_old_schema_table_is_not_patched(tmp_path):
    """desertion_no 키의 옛 테이블은 칸 추가로 맞추지 않고 멈춘다. test 는 recreate 로 다시 만든다, prod 는 거부."""
    from pyiceberg.catalog.sql import SqlCatalog
    from pyiceberg.schema import Schema
    from pyiceberg.types import NestedField, StringType

    catalog = SqlCatalog("o", uri=f"sqlite:///{tmp_path}/o.db", warehouse=f"file://{tmp_path}/wh")
    env = gc.GoldEnvironment("test", "rebom_gold", "_t", f"file://{tmp_path}/wh/gold")
    catalog.create_namespace("rebom_gold")
    old = Schema(NestedField(1, "desertion_no", StringType(), required=True))
    catalog.create_table(env.table(gc.TAG_TABLE), schema=old, location=env.location(gc.TAG_TABLE))
    with pytest.raises(gc.OldSchemaError):
        gc.create_tables(env, catalog)
    changed = gc.create_tables(env, catalog, recreate=True)
    assert any(c.startswith("다시 만듦") for c in changed)
    names = {f.name for f in catalog.load_table(env.table(gc.TAG_TABLE)).schema().fields}
    assert {"subject_type", "subject_key"} <= names and "desertion_no" not in names
    with pytest.raises(ValueError, match="RECREATE_TEST_ONLY"):
        gc.create_tables(gc.GoldEnvironment("prod", "rebom_gold", "", "x"), catalog, recreate=True)


def test_silver_image_table_is_photo_pipeline_table():
    """태깅·사진 임베딩·글 카드는 rescue_ingest 사진 처리가 쓰는 표(auto_b_test)를 읽는다."""
    from rebom.storage.silver.environment_config import get_silver_environment

    env = get_silver_environment("test")
    assert env.image_table == "rebom_silver.silver_animal_image_auto_b_test"
    assert not hasattr(env, "photo_table")


def test_read_silver_filters_protecting_dogs(tmp_path):
    import pyarrow as pa
    from pyiceberg.catalog.sql import SqlCatalog

    catalog = SqlCatalog("s", uri=f"sqlite:///{tmp_path}/s.db", warehouse=f"file://{tmp_path}/wh")
    catalog.create_namespace("rebom_silver")
    notices = pa.table({
        "desertion_no": ["1", "2", "3", "4"],
        "species_code": ["417000", "417000", "422400", "417000"],
        "status_group": ["PROTECTING", "CLOSED", "PROTECTING", "PROTECTING"],
    })
    images = pa.table({
        "desertion_no": ["1", "1", "2", "3"],
        "image_slot": pa.array([1, 2, 1, 1], pa.int32()),
        "original_s3_key": ["k1", "k2", "k3", "k4"],
        "sha256": ["a" * 64, "b" * 64, "c" * 64, "d" * 64],
    })
    catalog.create_table("rebom_silver.notice", schema=notices.schema).append(notices)
    catalog.create_table("rebom_silver.image", schema=images.schema).append(images)

    class Env:
        notice_table = "rebom_silver.notice"
        image_table = "rebom_silver.image"

    dogs, rows = rpt.read_silver(catalog, Env)
    assert sorted(dogs) == ["1", "4"]
    assert {(r["desertion_no"], r["image_slot"]) for r in rows} == {("1", 1), ("1", 2)}
    targets, no_photo = rpt.select_targets(dogs, rows, [])
    assert [(t.desertion_nos, t.s3_key, t.sha256) for t in targets] == [(("1",), "k1", "a" * 64)] and no_photo == ["4"]


def test_silver_cutoff_date_means_end_of_day_kst():
    from datetime import datetime

    at, inclusive = rpt.silver_cutoff("2026-11-23")
    assert at == datetime(2026, 11, 24, tzinfo=rpt.KST) and inclusive is False
    at, inclusive = rpt.silver_cutoff("2026-11-23T18:00:00")
    assert at == datetime(2026, 11, 23, 18, tzinfo=rpt.KST) and inclusive is True
    assert rpt.silver_cutoff("") is None and rpt.silver_cutoff(None) is None


def test_read_silver_pins_snapshot_before_cutoff(tmp_path):
    """11/23 기준으로 고르면 그 뒤에 들어온 공고·사진은 대상에 들지 않는다."""
    import time
    from datetime import UTC, datetime

    import pyarrow as pa
    from pyiceberg.catalog.sql import SqlCatalog

    catalog = SqlCatalog("s", uri=f"sqlite:///{tmp_path}/s.db", warehouse=f"file://{tmp_path}/wh")
    catalog.create_namespace("rebom_silver")
    notice = lambda nos: pa.table({"desertion_no": nos, "species_code": ["417000"] * len(nos),
                                   "status_group": ["PROTECTING"] * len(nos)})
    image = lambda nos: pa.table({"desertion_no": nos, "image_slot": pa.array([1] * len(nos), pa.int32()),
                                  "original_s3_key": [f"k{n}" for n in nos], "sha256": [n * 64 for n in nos]})
    notices = catalog.create_table("rebom_silver.notice", schema=notice(["1"]).schema)
    images = catalog.create_table("rebom_silver.image", schema=image(["1"]).schema)
    notices.append(notice(["1"]))
    images.append(image(["1"]))
    first_ms = max(notices.current_snapshot().timestamp_ms, images.current_snapshot().timestamp_ms)
    time.sleep(0.01)  # 두 번째 스냅샷이 확실히 뒤 밀리초에 커밋되게
    notices.append(notice(["2"]))
    images.append(image(["2"]))

    class Env:
        notice_table = "rebom_silver.notice"
        image_table = "rebom_silver.image"

    used: dict = {}
    cutoff = (datetime.fromtimestamp(first_ms / 1000, UTC), True)  # 첫 스냅샷까지(포함)
    dogs, rows = rpt.read_silver(catalog, Env, cutoff, used)
    assert dogs == ["1"] and [r["desertion_no"] for r in rows] == ["1"]
    assert set(used) == {"rebom_silver.notice", "rebom_silver.image"}
    dogs, _ = rpt.read_silver(catalog, Env)
    assert sorted(dogs) == ["1", "2"]


def test_read_silver_fails_without_snapshot_before_cutoff(tmp_path):
    from datetime import datetime

    import pyarrow as pa
    import pytest
    from pyiceberg.catalog.sql import SqlCatalog

    catalog = SqlCatalog("s", uri=f"sqlite:///{tmp_path}/s.db", warehouse=f"file://{tmp_path}/wh")
    catalog.create_namespace("rebom_silver")
    t = pa.table({"desertion_no": ["1"], "species_code": ["417000"], "status_group": ["PROTECTING"]})
    catalog.create_table("rebom_silver.notice", schema=t.schema).append(t)

    class Env:
        notice_table = "rebom_silver.notice"
        image_table = "rebom_silver.notice"

    with pytest.raises(ValueError, match="이전 스냅샷이 없습니다"):
        rpt.read_silver(catalog, Env, (datetime(2020, 1, 1, tzinfo=rpt.KST), False))


def test_create_tables_adds_missing_columns_to_existing_table(tmp_path):
    from pyiceberg.catalog.sql import SqlCatalog
    from pyiceberg.schema import Schema

    catalog = SqlCatalog("t", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    env = gc.GoldEnvironment("unit", "rebom_gold", "_unit", f"file://{tmp_path}/wh/gold")
    gc.create_tables(env, catalog)
    name = env.table(gc.REQUEST_LOG_TABLE)
    table = catalog.load_table(name)
    with table.update_schema() as update:
        update.delete_column("finish_reason")
        update.delete_column("temperature")

    changed = gc.create_tables(env, catalog)
    assert changed == [f"칸 추가 {name}: temperature, finish_reason"]
    fields = {f.name for f in catalog.load_table(name).schema().fields}
    assert {"temperature", "finish_reason", "native_finish_reason"} <= fields
    assert gc.create_tables(env, catalog) == []
    assert isinstance(catalog.load_table(name).schema(), Schema)


def test_read_inference_log_reads_only_photo_tagging_rows_and_keeps_detail(sink):
    """처리 기록은 임베딩과 같이 쓰는 공용 로그다. 태깅은 자기 task 행만 읽고, 실패 근거는 detail 에 남는다."""
    ts = datetime.now(UTC)
    embedding = {
        "subject_type": "photo", "subject_key": "e" * 64, "task": "image_embedding", "version": "m/p",
        "input_hash": "e" * 64, "status": "READ_ERROR", "attempts": 1, "tag_count": None,
        "last_error": "read_error:NoSuchKey", "detail": '{"s3_key": "k", "error": "NoSuchKey"}',
        "gold_run_id": "r", "gold_updated_at": ts,
    }
    failed = rpt.InputProblem("SHA_MISMATCH", "sha_mismatch", {"s3_key": "k/1.jpg"})
    target = rpt.Target("1" * 64, "k/1.jpg", (rpt.trait_tags.TASK,), ("1",))
    outcomes, _ = rpt.tag_photos([(target, failed)], client=None, cfg=rpt.TaggerConfig(run_id="r"))
    sink.write([], [], [embedding, *rpt.inference_rows(outcomes, {}, "r", ts)], [])

    rows = sink.read_inference_log()
    assert [(r["task"], r["status"], r["last_error"]) for r in rows] == [(rpt.trait_tags.TASK, "SHA_MISMATCH", "sha_mismatch")]
    assert json.loads(rows[0]["detail"]) == {"s3_key": "k/1.jpg"}
