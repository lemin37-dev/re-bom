"""사진 임베딩의 Silver READY 입력(--publish-id) 테스트. 로컬 카탈로그와 가짜 S3·Gemini 를 쓴다."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from rebom.embedding import cli, gemini, manifest as mf, sources, staging
from tests.test_embedding_gold_store import RecordedEmbedder, gold  # noqa: F401 - fixture
from tests.test_embedding_pipeline import FakeEmbedder, make_world

NOTICE, PHOTO = "rebom_silver.notice_unit", "rebom_silver.photo_unit"
PUBLISH = ["--publish-id", "p1", "--publish-bucket", "test-bucket", "--publish-prefix", "publish"]


@pytest.fixture
def world(monkeypatch, gold):  # noqa: F811
    import pyarrow as pa
    from pyiceberg.schema import Schema
    from pyiceberg.types import IntegerType, NestedField, StringType

    from rebom.storage.silver import environment_config

    catalog, env, table = gold
    s3 = make_world()
    current = mf.latest_photos(mf.load_manifests(s3, "test-bucket"))
    numbers = sorted({k[0] for k in current})

    def create(name, columns):
        catalog.create_namespace_if_not_exists("rebom_silver")
        return catalog.create_table(name, Schema(*[
            NestedField(i, c, IntegerType() if c == "image_slot" else StringType(), required=False)
            for i, c in enumerate(columns, 1)]))

    notice, photo = create(NOTICE, ["desertion_no", "status_group"]), create(PHOTO, list(sources.SILVER_FIELDS))

    def write(t, rows, how="append"):
        getattr(t, how)(pa.Table.from_pylist(rows, schema=t.schema().as_arrow()))

    ended = numbers[-1]
    write(notice, [{"desertion_no": n, "status_group": "CLOSED" if n == ended else "PROTECTING"} for n in numbers])
    write(photo, [{"desertion_no": p.desertion_no, "image_slot": p.slot, "original_s3_key": p.s3_key, "sha256": p.sha256}
                  for p in current.values()])
    s3.objects["publish/p1.json"] = json.dumps({
        "publish_id": "p1", "status": "READY", "bronze_run_id": "r1", "notice_run_id": "r1", "photo_run_id": "photo-r1",
        "notice_table": NOTICE, "photo_table": PHOTO,
        "notice_snapshot_id": notice.current_snapshot().snapshot_id,
        "photo_snapshot_id": photo.current_snapshot().snapshot_id,
    }).encode()
    silver = SimpleNamespace(notice_table=NOTICE, image_table=PHOTO)
    monkeypatch.setattr(environment_config, "get_silver_environment", lambda name: silver)
    monkeypatch.setenv("REBOM_S3_BUCKET", "test-bucket")
    monkeypatch.setattr(cli, "_s3", lambda: s3)
    monkeypatch.setattr(gemini, "GeminiEmbedder", RecordedEmbedder)
    FakeEmbedder.calls = []
    return SimpleNamespace(s3=s3, current=current, ended=ended, table=table, catalog=catalog, env=env, silver=silver,
                           notice=notice, photo=photo, write=write)


def embed(*extra):
    return cli.main(["embed", "--limit", "10", "--gold-env", "test", *PUBLISH, *extra])


def run_inputs(world):
    from rebom.common import gold_catalog as gc

    return world.table(gc.RUN_INPUT_TABLE)


def test_reads_pinned_snapshots_and_only_protected_notices(world):
    from rebom.common import gold_catalog as gc

    # 발행 뒤에 표가 바뀌어도 발행본의 스냅샷을 읽는다
    world.write(world.notice, [{"desertion_no": world.ended, "status_group": "PROTECTING"}], "overwrite")
    world.write(world.photo, [{"desertion_no": world.ended, "image_slot": 1,
                               "original_s3_key": f"{mf.IMAGES_PREFIX}{world.ended}/popfile1.jpg", "sha256": "f" * 64}],
                "overwrite")
    assert embed() == 0
    images = world.table(gc.IMAGE_EMBEDDING_TABLE)
    protected = {p.sha256 for k, p in world.current.items() if k[0] != world.ended}
    logs = {r["subject_key"]: r["status"] for r in world.table(gc.INFERENCE_LOG_TABLE)}
    assert set(logs) == protected  # 종료 공고에만 있는 사진은 보내지 않는다
    assert {r["subject_key"] for r in images} == {s for s, status in logs.items() if status == "OK"}


def test_links_run_to_publish_id_and_keeps_backup_in_work_folder(world):
    from rebom.common import gold_catalog as gc

    assert embed() == 0
    [row] = run_inputs(world)
    assert (row["publish_id"], row["task"]) == ("p1", "image_embedding")
    assert row["version"] == "gemini-embedding-2:768/exif-rgb-max1024-jpeg90"
    for base in (gc.IMAGE_EMBEDDING_TABLE, gc.INFERENCE_LOG_TABLE, gc.REQUEST_LOG_TABLE):
        assert {r["gold_run_id"] for r in world.table(base)} == {row["gold_run_id"]}, base
    # 백업 parquet 은 작업 폴더에만 (옛 방식이 읽는 gold/image_embedding/ 에는 두지 않는다)
    backups = [k for k in world.s3.objects if k.endswith(".parquet")]
    assert backups and all(k.startswith(f"gold/_work_unit/image_embedding/{row['gold_run_id']}/") for k in backups)
    assert all("photo_run_id" not in r for k in backups for r in staging.read_parquet(world.s3.objects[k]))
    [key] = [k for k in world.s3.objects if k.endswith("summary.json")]
    assert key == f"gold/_work_unit/image_embedding/{row['gold_run_id']}/summary.json"
    assert json.loads(world.s3.objects[key])["publish_id"] == "p1"


def test_run_without_new_photos_is_still_recorded(world, capsys):
    assert embed() == 0
    calls = len(FakeEmbedder.calls)
    assert embed() == 0
    assert "보낼 사진이 없습니다" in capsys.readouterr().out
    assert len(FakeEmbedder.calls) == calls  # 다시 부르지 않음
    rows = run_inputs(world)
    assert len(rows) == 2 and len({r["gold_run_id"] for r in rows}) == 2 and {r["publish_id"] for r in rows} == {"p1"}


def test_dry_run_writes_nothing(world):
    before = dict(world.s3.objects)
    assert embed("--dry-run") == 0
    assert run_inputs(world) == [] and FakeEmbedder.calls == [] and world.s3.objects == before


def test_stops_before_model_when_tables_are_not_of_gold_env(world):
    world.silver.image_table = "rebom_silver.other"
    with pytest.raises(ValueError, match="환경의 표가 아님"):
        embed()
    assert FakeEmbedder.calls == [] and run_inputs(world) == []
    assert embed("--dry-run") == 0  # 읽기만 하는 확인은 됨


def test_stops_before_model_without_run_input_table(world):
    from rebom.common import gold_catalog as gc
    from rebom.embedding.gold_store import LogSchemaError

    world.catalog.drop_table(world.env.table(gc.RUN_INPUT_TABLE))
    with pytest.raises(LogSchemaError, match="execution"):
        embed()
    assert FakeEmbedder.calls == []


def test_empty_publication_is_an_error_not_a_success(world):
    record = json.loads(world.s3.objects["publish/p1.json"])
    world.write(world.notice, [{"desertion_no": "none", "status_group": "PROTECTING"}], "overwrite")
    record["notice_snapshot_id"] = world.catalog.load_table(NOTICE).current_snapshot().snapshot_id
    world.s3.objects["publish/p1.json"] = json.dumps(record).encode()
    with pytest.raises(ValueError, match="사진이 없음"):
        embed()
    assert run_inputs(world) == []


@pytest.mark.parametrize("argv,msg", [
    (["--publish-id", "p1", "--gold-env", "test"], "publish-bucket"),
    (PUBLISH, "gold-env"),
    ([*PUBLISH, "--gold-env", "test", "--source", "silver"], "--source"),
    (["--publish-bucket", "b", "--gold-env", "test"], "--publish-id"),
])
def test_rejects_incomplete_options(world, argv, msg):
    with pytest.raises(SystemExit, match=msg):
        cli.main(["embed", "--limit", "1", *argv])


def test_compare_sources_limits_bronze_to_protected(world, capsys):
    assert cli.main(["compare-sources", *PUBLISH, "--gold-env", "test"]) == 0
    out = capsys.readouterr().out
    result = json.loads(out[out.index("{"):])
    assert result["switch_ready"] is True
    assert result["bronze_photos"] == sum(k[0] != world.ended for k in world.current)


def test_new_parquet_has_no_photo_run_id(world):
    p = next(iter(world.current.values()))
    row = staging.photo_row(p, [0.0] * 4, "api", "run", "prep", "now")
    assert "photo_run_id" not in row and "photo_run_id" not in staging.COLUMNS
    assert set(staging.read_parquet(staging.to_parquet([row], 4))[0]) == set(staging.COLUMNS)
