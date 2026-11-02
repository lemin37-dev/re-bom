"""S3 업로드(rebom.ingestion.storage)와 증분 수집의 저장 순서 테스트. 실제 S3 는 쓰지 않고 가짜 클라이언트를 쓴다."""
import hashlib
import json
from pathlib import Path

import pytest

from rebom.ingestion import rescue_incremental as ri
from rebom.ingestion.storage import LocalStore, S3Store, UploadError, make_store

from test_rescue_incremental import run_main, seed_scenario


class NotFound(Exception):
    response = {"Error": {"Code": "404"}}


class FakeS3Client:
    """put_object, head_object, get_object 만 흉내 낸다. fail_key 가 들어간 키를 올리면 오류를 낸다."""

    def __init__(self, fail_key=None, corrupt_key=None):
        self.objects = {}  # 키 -> (바이트, 메타데이터)
        self.put_order = []
        self.fail_key = fail_key
        self.corrupt_key = corrupt_key

    def put_object(self, Bucket, Key, Body, Metadata=None):
        if self.fail_key and self.fail_key in Key:
            raise ConnectionError("업로드 실패")
        body = Body[:-1] if self.corrupt_key and self.corrupt_key in Key else Body
        self.objects[Key] = (body, dict(Metadata or {}))
        self.put_order.append(Key)

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise NotFound()
        body, meta = self.objects[Key]
        return {"ContentLength": len(body), "Metadata": meta}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise NotFound()
        data = self.objects[Key][0]
        return {"Body": type("Body", (), {"read": lambda self_: data})()}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("rebom.ingestion.storage.time.sleep", lambda s: None)


def test_s3_store_uploads_with_checksum_and_blocks_overwrite(tmp_path):
    client = FakeS3Client()
    store = S3Store(client, "bucket", prefix="team")
    f = tmp_path / "a.jsonl.gz"
    f.write_bytes(b"hello")
    store.put_file(f, "bronze/rescue/raw/x/a.jsonl.gz", checksum=hashlib.sha256(b"hello").hexdigest())
    assert "team/bronze/rescue/raw/x/a.jsonl.gz" in client.objects
    with pytest.raises(UploadError):
        store.put_file(f, "bronze/rescue/raw/x/a.jsonl.gz")  # 덮어쓰지 않는다


def test_s3_store_rejects_wrong_checksum_before_upload(tmp_path):
    client = FakeS3Client()
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    with pytest.raises(UploadError):
        S3Store(client, "bucket").put_file(f, "k", checksum="0" * 64)
    assert client.objects == {}


def test_s3_store_detects_content_mismatch_after_upload(tmp_path):
    client = FakeS3Client(corrupt_key="k")
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    with pytest.raises(UploadError):
        S3Store(client, "bucket").put_file(f, "k")


def test_s3_store_retries_then_gives_up_without_leaking_message(tmp_path):
    client = FakeS3Client(fail_key="k")
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    with pytest.raises(UploadError) as e:
        S3Store(client, "bucket", retries=2).put_file(f, "k")
    assert "ConnectionError" in str(e.value)


def test_get_bytes_returns_none_for_missing_key():
    assert S3Store(FakeS3Client(), "bucket").get_bytes("state/none.json") is None


def test_local_store_roundtrip_and_no_overwrite(tmp_path):
    store = LocalStore(tmp_path)
    store.put_bytes("state/s.json", b"1")
    store.put_bytes("state/s.json", b"2", overwrite=True)
    assert store.get_bytes("state/s.json") == b"2"
    with pytest.raises(UploadError):
        store.put_bytes("state/s.json", b"3", overwrite=False)


def test_make_store_uses_local_without_bucket(monkeypatch, tmp_path):
    monkeypatch.delenv("REBOM_S3_BUCKET", raising=False)
    assert isinstance(make_store(tmp_path), LocalStore)


def test_run_uploads_manifest_last_then_updates_state():
    client = FakeS3Client()
    store = S3Store(client, "bucket")
    run_main(store, seed_scenario(store, protect_extra=0))
    keys = client.put_order
    manifest_idx = [i for i, k in enumerate(keys) if k.endswith("/manifest.json")]
    assert len(manifest_idx) == 1
    run_keys = [k for k in keys if k.startswith("bronze/rescue/raw/dt=")]
    assert run_keys[-1].endswith("/manifest.json")  # manifest 가 마지막
    assert keys[-1] == ri.STATE_KEY  # 상태는 업로드가 모두 끝난 뒤
    manifest = json.loads(client.objects[run_keys[-1]][0])
    for item in manifest["files"]:
        data, meta = client.objects[run_keys[-1].rsplit("/", 1)[0] + "/" + item["file"]]
        assert hashlib.sha256(data).hexdigest() == item["checksum"] == meta["sha256"]


def test_failed_upload_keeps_previous_state():
    client = FakeS3Client(fail_key="list_protect.jsonl.gz")
    store = S3Store(client, "bucket", retries=1)
    fake_call = seed_scenario(store, protect_extra=0)
    before = client.objects[ri.STATE_KEY][0]
    with pytest.raises(UploadError):
        run_main(store, fake_call)
    assert client.objects[ri.STATE_KEY][0] == before  # 마지막 성공일이 앞으로 가지 않았다
    assert not any(k.endswith("/manifest.json") for k in client.objects)  # manifest 는 올라가지 않았다


def test_local_store_default_keeps_old_layout(tmp_path):
    store = LocalStore(tmp_path)
    run_main(store, seed_scenario(store, protect_extra=0))
    assert (tmp_path / "state" / "rescue_state.json").exists()
    assert list(Path(tmp_path / "bronze" / "rescue" / "raw").glob("dt=*/run_id=*/manifest.json"))
