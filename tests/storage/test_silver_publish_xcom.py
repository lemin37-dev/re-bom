"""Exercise the publication command's XCom handoff without AWS."""

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("mode", ["new", "reuse", "blocked", "save_failed"])
def test_only_confirmed_publication_writes_xcom(mode, tmp_path, monkeypatch):
    pytest.importorskip("pyiceberg")
    from botocore.exceptions import ClientError
    from rebom.storage.silver import photo_snapshot

    path = Path(__file__).resolve().parents[2] / "scripts/storage/rebom_publish_silver_gold.py"
    spec = importlib.util.spec_from_file_location("silver_publish_cli", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    output = tmp_path / "xcom" / "return.json"
    run_id = "20261125T030005-5c7541"
    publish_id = f"silver_{run_id}"
    record = {
        "publish_id": publish_id, "bronze_run_id": run_id, "notice_run_id": run_id,
        "photo_run_id": "photo-run", "notice_table": "silver.notice", "photo_table": "silver.photo",
        "status": "READY", "notice_snapshot_id": 11, "photo_snapshot_id": 22,
        "published_at": "2026-11-25T03:30:00+09:00",
    }
    objects = {
        "photos/photo-run.json": {
            "run_id": "photo-run", "manifest_key": "photo-manifest", "manifest_status": "ok",
            "processing_status": "APPLIED", "needs_review": False, "failed_count": 0,
            "snapshot_checksum": "test-checksum", "current_snapshot_id": 22,
        },
        "photo-manifest": {"status": "ok", "run_id": "photo-run", "failed": []},
        f"bronze/rescue/raw/dt=2026-11-25/run_id={run_id}/manifest.json": {
            "run_id": run_id, "status": "ok", "fetched_at": "2026-11-25T03:00:05+09:00",
        },
    }
    if mode == "reuse":
        objects[f"publish/{publish_id}.json"] = record

    def get_object(*, Bucket, Key):
        if Key not in objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(json.dumps(objects[Key]).encode())}

    s3 = SimpleNamespace(get_object=get_object)
    snapshot = SimpleNamespace(snapshot_id=22)
    table = SimpleNamespace(
        snapshot_by_id=lambda _: snapshot,
        scan=lambda **_: SimpleNamespace(to_arrow=lambda: SimpleNamespace(to_pylist=lambda: [])),
        metadata=SimpleNamespace(snapshots=[snapshot]),
    )
    monkeypatch.setattr(cli.boto3, "Session", lambda **_: SimpleNamespace(client=lambda _: s3))
    monkeypatch.setattr(cli, "get_aws_catalog", lambda: SimpleNamespace(load_table=lambda _: table))
    monkeypatch.setattr(cli, "find_completed_notice_snapshot_id", lambda *_: 11)
    monkeypatch.setattr(cli, "verify_photo_bronze_run", lambda *_: mode != "blocked")
    monkeypatch.setattr(photo_snapshot, "load_photo_snapshot",
                        lambda *_: {"checksum": "test-checksum", "rows": {}})
    monkeypatch.setattr(cli, "reserve_publish_seq", lambda *_: 2)
    saved = []

    def save(_s3, _bucket, _prefix, publication):
        assert not output.exists()
        if mode == "save_failed":
            raise RuntimeError("save failed")
        saved.append(publication["publish_id"])
        return "CREATED"

    monkeypatch.setattr(cli, "save_publish_record_s3", save)
    monkeypatch.setattr(sys, "argv", [
        str(path), "--bronze-run-id", run_id, "--photo-run-id", "photo-run",
        "--notice-table", "silver.notice", "--photo-table", "silver.photo",
        "--photo-prefix", "photos", "--publish-prefix", "publish",
        "--pod-auth", "--publish", "--publish-output", str(output),
    ])

    if mode == "blocked":
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == 2
    elif mode == "save_failed":
        with pytest.raises(RuntimeError, match="save failed"):
            cli.main()
    else:
        cli.main()
        assert json.loads(output.read_text(encoding="utf-8")) == {"publish_id": publish_id}

    assert saved == ([publish_id] if mode == "new" else [])
    assert output.exists() == (mode in {"new", "reuse"})
