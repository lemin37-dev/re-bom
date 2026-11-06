"""사진 수집(rebom.ingestion.rescue_photos) 테스트. 실제 네트워크와 S3 는 쓰지 않고 로컬 저장소와 가짜 내려받기를 쓴다."""
import gzip
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
import requests
from PIL import Image

from rebom.ingestion import rescue_photos as rp
from rebom.ingestion.storage import LocalStore, UploadError

NOW = datetime(2026, 11, 4, 10, 0, tzinfo=timezone(timedelta(hours=9)))
RUN_ID = "20261104T100101-0a89a3"


def image_bytes(fmt="JPEG", size=(800, 600), color=(200, 120, 60)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format=fmt)
    return out.getvalue()


def seed_bronze(store, rows_notice, rows_protect=(), status="ok", run_id=RUN_ID):
    prefix = f"{rp.RAW_PREFIX}/dt={run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}/run_id={run_id}"
    files = []
    for kind, rows in (("list_notice", rows_notice), ("list_protect", rows_protect)):
        raw = gzip.compress("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8"))
        store.put_bytes(f"{prefix}/{kind}.jsonl.gz", raw)
        files.append({"kind": kind, "file": f"{kind}.jsonl.gz", "checksum": hashlib.sha256(raw).hexdigest()})
    store.put_bytes(f"{prefix}/manifest.json", json.dumps({"status": status, "files": files}).encode("utf-8"))


def row(no, p1=None, p2=None, happen="20261101"):
    r = {"desertionNo": no, "happenDt": happen, "processState": "보호중"}
    if p1:
        r["popfile1"] = p1
    if p2:
        r["popfile2"] = p2
    return r


class FakeFetch:
    """주소별로 (바이트, 사유)를 돌려주는 가짜 내려받기. 몇 번 불렸는지 센다."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        return self.table[url]


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path)


def state_of(store):
    return json.loads(store.get_bytes(rp.STATE_KEY).decode("utf-8"))


def test_new_notice_saves_original_and_serving_files(store):
    seed_bronze(store, [row("111", "http://x/a", "http://x/b")])
    jpg, png = image_bytes("JPEG"), image_bytes("PNG")
    fetch = FakeFetch({"http://x/a": (jpg, None), "http://x/b": (png, None)})
    out = rp.run_photos(store, fetch, RUN_ID, now=NOW)

    assert out["results"] == {"success": 2}
    # 원본은 받은 그대로, 확장자는 내용으로 판단
    assert store.get_bytes("bronze/rescue/images/111/popfile1.jpg") == jpg
    assert store.get_bytes("bronze/rescue/images/111/popfile2.png") == png
    # 서비스용은 따로 만든 WebP 와 썸네일
    full = Image.open(io.BytesIO(store.get_bytes("images/111/1.webp")))
    thumb = Image.open(io.BytesIO(store.get_bytes("images/111/1_thumb.webp")))
    assert full.format == "WEBP" and full.size == (800, 600)
    assert thumb.format == "WEBP" and thumb.size == (rp.THUMB_WIDTH, 300)
    st = state_of(store)["notices"]["111"]["slots"]
    assert st["1"]["status"] == "success" and st["1"]["ext"] == "jpg" and st["1"]["sha256"] == hashlib.sha256(jpg).hexdigest()
    assert st["2"]["ext"] == "png"


def test_small_image_thumbnail_is_not_upscaled(store):
    seed_bronze(store, [row("111", "http://x/a")])
    fetch = FakeFetch({"http://x/a": (image_bytes(size=(200, 100)), None)})
    rp.run_photos(store, fetch, RUN_ID, now=NOW)
    thumb = Image.open(io.BytesIO(store.get_bytes("images/111/1_thumb.webp")))
    assert thumb.size == (200, 100)


def row_slots(no, urls: dict, happen="20261101"):
    """{칸 번호: 주소} 로 공고 한 건을 만든다. 칸이 건너뛰어진 공고(1, 7, 8번만 채워짐)를 만들 때 쓴다."""
    r = {"desertionNo": no, "happenDt": happen, "processState": "보호중"}
    r.update({f"popfile{k}": u for k, u in urls.items()})
    return r


def test_empty_slots_are_not_recorded_and_photo_less_notice_is_marked_once(store):
    seed_bronze(store, [row("111", "http://x/a"), row("222")])  # 111: 1장, 222: 0장
    fetch = FakeFetch({"http://x/a": (image_bytes(), None)})
    out = rp.run_photos(store, fetch, RUN_ID, now=NOW)
    assert len(fetch.calls) == 1 and out["no_photos"] == 1
    st = state_of(store)["notices"]
    assert list(st["111"]["slots"]) == ["1"]  # 주소가 없는 칸(2~8)은 기록하지 않는다
    assert st["222"] == {"slots": {}, "no_photos": True}
    out = rp.run_photos(store, fetch, RUN_ID, now=NOW)  # 다시 돌려도 표시를 또 세지 않는다
    assert "no_photos" not in out


def test_all_filled_slots_up_to_eight_are_saved_with_popfile_numbers(store):
    urls = {k: f"http://x/{k}" for k in range(1, 9)}
    seed_bronze(store, [row_slots("111", urls)])
    fetch = FakeFetch({u: (image_bytes(), None) for u in urls.values()})
    out = rp.run_photos(store, fetch, RUN_ID, now=NOW, photo_run_id="r1")
    assert out["results"] == {"success": 8} and len(fetch.calls) == 8
    for k in range(1, 9):
        assert store.get_bytes(f"bronze/rescue/images/111/popfile{k}.jpg")
        assert store.get_bytes(f"images/111/{k}.webp") and store.get_bytes(f"images/111/{k}_thumb.webp")
    assert sorted(f["slot"] for f in read_manifest(store, "r1")["files"]) == list(range(1, 9))


def test_skipped_slots_keep_their_popfile_numbers(store):
    """popfile2 가 비고 7, 8번에 사진이 있는 공고(11/4 기준 243건)는 slot 1, 7, 8 로 저장된다."""
    urls = {1: "http://x/1", 7: "http://x/7", 8: "http://x/8"}
    seed_bronze(store, [row_slots("111", urls)])
    fetch = FakeFetch({u: (image_bytes(), None) for u in urls.values()})
    rp.run_photos(store, fetch, RUN_ID, now=NOW, photo_run_id="r1")
    assert list(state_of(store)["notices"]["111"]["slots"]) == ["1", "7", "8"]
    assert store.get_bytes("bronze/rescue/images/111/popfile7.jpg") and store.get_bytes("images/111/8.webp")
    assert store.get_bytes("bronze/rescue/images/111/popfile2.jpg") is None and store.get_bytes("images/111/2.webp") is None


def test_failed_late_slot_is_retried_like_any_other_slot(store):
    urls = {1: "http://x/1", 7: "http://x/7"}
    seed_bronze(store, [row_slots("111", urls)])
    rp.run_photos(store, FakeFetch({"http://x/1": (image_bytes(), None), "http://x/7": (None, "timeout")}), RUN_ID, now=NOW)
    fetch = FakeFetch({"http://x/7": (image_bytes(), None)})
    rp.run_photos(store, fetch, RUN_ID, now=NOW)
    slots = state_of(store)["notices"]["111"]["slots"]
    assert fetch.calls == ["http://x/7"]  # 성공한 1번은 건너뛰고 실패한 7번만 다시 받는다
    assert slots["7"]["status"] == "success" and slots["7"]["attempts"] == 2


def test_state_does_not_grow_with_empty_slots(store):
    seed_bronze(store, [row(f"{i:03d}", f"http://x/{i}") for i in range(50)])  # 공고 50건, 사진은 각 1장
    fetch = FakeFetch({f"http://x/{i}": (image_bytes(size=(40, 30)), None) for i in range(50)})
    rp.run_photos(store, fetch, RUN_ID, now=NOW)
    recorded = sum(len(n["slots"]) for n in state_of(store)["notices"].values())
    assert recorded == 50  # 8칸이었다면 400개가 됐을 것


def test_notice_and_protect_lists_are_both_read_without_duplicates(store):
    seed_bronze(store, [row("111", "http://x/a")], [row("222", "http://x/b"), row("111", "http://x/a")])
    fetch = FakeFetch({"http://x/a": (image_bytes(), None), "http://x/b": (image_bytes(), None)})
    out = rp.run_photos(store, fetch, RUN_ID, now=NOW)
    assert out["bronze_notices"] == 2 and sorted(fetch.calls) == ["http://x/a", "http://x/b"]


def mpo_bytes():
    """휴대폰 사진처럼 한 파일에 두 장이 든 MPO. 열면 Pillow 는 format 을 "MPO" 로 알려 주지만 파일은 JPEG 이다."""
    out = io.BytesIO()
    Image.new("RGB", (800, 600), (200, 100, 50)).save(out, format="MPO", save_all=True, append_images=[Image.new("RGB", (800, 600), (50, 100, 200))])
    return out.getvalue()


def test_mpo_phone_photo_is_stored_as_jpg(store):
    """11/4 표본에서 정상 JPEG 9장이 broken_file 로 실패했던 원인(MPO 를 지원하지 않음)을 막는다."""
    data = mpo_bytes()
    assert Image.open(io.BytesIO(data)).format == "MPO"
    seed_bronze(store, [row("111", "http://x/a")])
    out = rp.run_photos(store, FakeFetch({"http://x/a": (data, None)}), RUN_ID, now=NOW)
    assert out["results"] == {"success": 1}
    assert store.get_bytes("bronze/rescue/images/111/popfile1.jpg") == data  # 받은 그대로, 확장자만 jpg
    assert store.get_bytes("images/111/1.webp") and store.get_bytes("images/111/1_thumb.webp")
    assert state_of(store)["notices"]["111"]["slots"]["1"]["ext"] == "jpg"


def test_readable_but_unsupported_format_is_not_called_broken(store):
    out = io.BytesIO()
    Image.new("RGB", (40, 30)).save(out, format="PPM")  # 사진으로는 읽히지만 허용 형식 목록에 없음
    seed_bronze(store, [row("111", "http://x/a")])
    rp.run_photos(store, FakeFetch({"http://x/a": (out.getvalue(), None)}), RUN_ID, now=NOW)
    rec = state_of(store)["notices"]["111"]["slots"]["1"]
    assert rec["status"] == "failed" and rec["reason"] == "unsupported_format"


@pytest.mark.parametrize("payload", [b"<html>error</html>", b"", image_bytes()[:200]])
def test_broken_file_is_a_failure_and_nothing_is_stored(store, payload):
    seed_bronze(store, [row("111", "http://x/a")])
    fetch = FakeFetch({"http://x/a": (payload, None)})
    out = rp.run_photos(store, fetch, RUN_ID, now=NOW)
    rec = state_of(store)["notices"]["111"]["slots"]["1"]
    assert out["reasons"] == {"broken_file": 1}
    assert rec["status"] == "failed" and rec["attempts"] == 1 and rec["reason"] == "broken_file"
    assert store.get_bytes("images/111/1.webp") is None
    assert not (store.root / "bronze/rescue/images/111").exists()


@pytest.mark.parametrize("reason", ["not_found", "timeout", "server_error", "http_error", "network_error"])
def test_download_failure_reason_is_recorded(store, reason):
    seed_bronze(store, [row("111", "http://x/a")])
    rp.run_photos(store, FakeFetch({"http://x/a": (None, reason)}), RUN_ID, now=NOW)
    rec = state_of(store)["notices"]["111"]["slots"]["1"]
    assert rec["status"] == "failed" and rec["reason"] == reason


def test_failed_slot_is_retried_next_run_and_gives_up_after_max_attempts(store):
    seed_bronze(store, [row("111", "http://x/a")])
    fetch = FakeFetch({"http://x/a": (None, "not_found")})
    for expected in ("failed", "failed", "gave_up"):
        rp.run_photos(store, fetch, RUN_ID, now=NOW)
        assert state_of(store)["notices"]["111"]["slots"]["1"]["status"] == expected
    assert len(fetch.calls) == rp.MAX_ATTEMPTS
    rp.run_photos(store, fetch, RUN_ID, now=NOW)  # gave_up 은 다시 시도하지 않는다
    assert len(fetch.calls) == rp.MAX_ATTEMPTS


def test_retry_succeeds_and_keeps_attempt_count(store):
    seed_bronze(store, [row("111", "http://x/a")])
    rp.run_photos(store, FakeFetch({"http://x/a": (None, "timeout")}), RUN_ID, now=NOW)
    rp.run_photos(store, FakeFetch({"http://x/a": (image_bytes(), None)}), RUN_ID, now=NOW)
    rec = state_of(store)["notices"]["111"]["slots"]["1"]
    assert rec["status"] == "success" and rec["attempts"] == 2 and rec["reason"] is None


def test_success_is_skipped_on_next_run(store):
    seed_bronze(store, [row("111", "http://x/a")])
    fetch = FakeFetch({"http://x/a": (image_bytes(), None)})
    rp.run_photos(store, fetch, RUN_ID, now=NOW)
    out = rp.run_photos(store, fetch, RUN_ID, now=NOW)
    assert len(fetch.calls) == 1 and out["to_process"] == 0


def test_only_new_notices_are_downloaded_on_a_later_run(store):
    seed_bronze(store, [row("111", "http://x/a")])
    fetch = FakeFetch({"http://x/a": (image_bytes(), None), "http://x/b": (image_bytes(), None)})
    rp.run_photos(store, fetch, RUN_ID, now=NOW)
    run2 = "20261105T100101-bbbbbb"
    seed_bronze(store, [row("111", "http://x/a"), row("222", "http://x/b")], run_id=run2)
    rp.run_photos(store, fetch, run2, now=NOW)
    assert fetch.calls == ["http://x/a", "http://x/b"]


def test_limit_and_dry_run(store):
    seed_bronze(store, [row("111", "http://x/a"), row("222", "http://x/b")])
    fetch = FakeFetch({"http://x/a": (image_bytes(), None), "http://x/b": (image_bytes(), None)})
    out = rp.run_photos(store, fetch, RUN_ID, dry_run=True, now=NOW)
    assert out["dry_run"] and out["to_process"] == 2 and fetch.calls == []
    assert store.get_bytes(rp.STATE_KEY) is None  # dry-run 은 아무것도 쓰지 않는다
    out = rp.run_photos(store, fetch, RUN_ID, limit=1, now=NOW)
    assert out["this_run"] == 1 and len(fetch.calls) == 1


class FailingStore(LocalStore):
    """n 번째 사진 원본을 올릴 때 업로드 오류를 낸다."""

    def __init__(self, root, fail_on):
        super().__init__(root)
        self.fail_on = fail_on
        self.original_puts = 0

    def put_bytes(self, key, data, overwrite=True):
        if key.startswith(rp.ORIGINAL_PREFIX):
            self.original_puts += 1
            if self.original_puts == self.fail_on:
                raise UploadError("업로드 실패")
        super().put_bytes(key, data, overwrite)


def test_upload_failure_saves_progress_and_next_run_continues(tmp_path):
    store = FailingStore(tmp_path, fail_on=2)
    seed_bronze(store, [row("111", "http://x/a"), row("222", "http://x/b")])
    fetch = FakeFetch({"http://x/a": (image_bytes(), None), "http://x/b": (image_bytes(), None)})
    out = rp.run_photos(store, fetch, RUN_ID, now=NOW)
    assert out["aborted"] and out["processed"] == 1
    notices = state_of(store)["notices"]  # 접수일이 같으면 번호가 큰 222 가 먼저 처리된다
    assert notices["222"]["slots"]["1"]["status"] == "success"
    assert "111" not in notices  # 올라가지 못한 공고는 기록에 없어서 다음 실행이 다시 받음
    out = rp.run_photos(LocalStore(tmp_path), fetch, RUN_ID, now=NOW)
    assert out["results"] == {"success": 1} and "aborted" not in out


def test_bad_desertion_no_is_skipped(store):
    seed_bronze(store, [row("../evil", "http://x/a")])
    fetch = FakeFetch({})
    out = rp.run_photos(store, fetch, RUN_ID, now=NOW)
    assert out["skipped_bad_id"] == 1 and fetch.calls == []


def test_bronze_run_with_wrong_checksum_or_status_stops(store):
    seed_bronze(store, [row("111", "http://x/a")], status="aborted")
    with pytest.raises(SystemExit):
        rp.load_bronze_rows(store, RUN_ID)
    run2 = "20261104T110101-cccccc"
    seed_bronze(store, [row("111", "http://x/a")], run_id=run2)
    key = f"{rp.RAW_PREFIX}/dt=2026-11-04/run_id={run2}/list_notice.jsonl.gz"
    store.put_bytes(key, gzip.compress(b'{"desertionNo": "999"}\n'))  # manifest 의 체크섬과 달라짐
    with pytest.raises(SystemExit):
        rp.load_bronze_rows(store, run2)
    with pytest.raises(SystemExit):
        rp.load_bronze_rows(store, "20261104T120000-dddddd")  # manifest 없음


# ----- http_fetch: 응답 코드와 오류를 사유로 바꾸는지 -----

class FakeResponse:
    def __init__(self, status=200, chunks=(b"abc",), error=None):
        self.status_code = status
        self.chunks = chunks
        self.error = error

    def iter_content(self, size):
        for c in self.chunks:
            yield c
        if self.error:
            raise self.error

    def close(self):
        pass


@pytest.mark.parametrize(
    "status,expected",
    [(404, "not_found"), (410, "not_found"), (500, "server_error"), (503, "server_error"), (403, "http_error"), (301, "http_error")],
)
def test_http_fetch_maps_status_codes(monkeypatch, status, expected):
    monkeypatch.setattr(rp.requests, "get", lambda *a, **k: FakeResponse(status))
    assert rp.http_fetch("http://x") == (None, expected)


def test_http_fetch_ok_and_network_errors(monkeypatch):
    monkeypatch.setattr(rp.requests, "get", lambda *a, **k: FakeResponse(200, (b"ab", b"cd")))
    assert rp.http_fetch("http://x") == (b"abcd", None)

    def raise_timeout(*a, **k):
        raise requests.Timeout()

    monkeypatch.setattr(rp.requests, "get", raise_timeout)
    assert rp.http_fetch("http://x") == (None, "timeout")

    def raise_conn(*a, **k):
        raise requests.ConnectionError()

    monkeypatch.setattr(rp.requests, "get", raise_conn)
    assert rp.http_fetch("http://x") == (None, "network_error")

    monkeypatch.setattr(rp.requests, "get", lambda *a, **k: FakeResponse(200, (b"ab",), error=requests.exceptions.ChunkedEncodingError()))
    assert rp.http_fetch("http://x") == (None, "network_error")  # 받는 도중 끊김


def test_http_fetch_rejects_oversized_response(monkeypatch):
    monkeypatch.setattr(rp, "MAX_BYTES", 5)
    monkeypatch.setattr(rp.requests, "get", lambda *a, **k: FakeResponse(200, (b"abc", b"def")))
    assert rp.http_fetch("http://x") == (None, "broken_file")


# ----- 실행별 인계 manifest (Silver 가 읽어 사진 레코드의 위치 칸을 채운다) -----

def read_manifest(store, photo_run_id, dt="2026-11-04"):
    raw = store.get_bytes(f"{rp.MANIFEST_PREFIX}/dt={dt}/run_id={photo_run_id}/manifest.json")
    return json.loads(raw.decode("utf-8")) if raw else None


def test_manifest_lists_stored_photos_with_paths_and_checksums(store):
    seed_bronze(store, [row("111", "http://x/a", "http://x/b")])
    jpg, png = image_bytes("JPEG"), image_bytes("PNG")
    fetch = FakeFetch({"http://x/a": (jpg, None), "http://x/b": (png, None)})
    out = rp.run_photos(store, fetch, RUN_ID, now=NOW, photo_run_id="r1")
    m = read_manifest(store, "r1")
    assert out["manifest"].endswith("run_id=r1/manifest.json")
    assert m["status"] == "ok" and m["bronze_run_id"] == RUN_ID and m["counts"]["stored"] == 2 and m["failed"] == []
    first = next(f for f in m["files"] if f["slot"] == 1)
    assert first == {
        "desertionNo": "111", "slot": 1, "original": "bronze/rescue/images/111/popfile1.jpg", "ext": "jpg",
        "bytes": len(jpg), "sha256": hashlib.sha256(jpg).hexdigest(),
        "serving": {"image": "images/111/1.webp", "thumb": "images/111/1_thumb.webp"},
    }
    assert next(f for f in m["files"] if f["slot"] == 2)["original"].endswith("popfile2.png")
    for f in m["files"]:  # manifest 에 적힌 경로는 실제로 저장돼 있다
        assert store.get_bytes(f["original"]) and store.get_bytes(f["serving"]["image"]) and store.get_bytes(f["serving"]["thumb"])


def test_manifest_has_only_photos_new_in_that_run(store):
    seed_bronze(store, [row("111", "http://x/a")])
    fetch = FakeFetch({"http://x/a": (image_bytes(), None), "http://x/b": (image_bytes(), None)})
    rp.run_photos(store, fetch, RUN_ID, now=NOW, photo_run_id="r1")
    run2 = "20261105T100101-bbbbbb"
    seed_bronze(store, [row("111", "http://x/a"), row("222", "http://x/b")], run_id=run2)
    rp.run_photos(store, fetch, run2, now=NOW + timedelta(days=1), photo_run_id="r2")
    assert [f["desertionNo"] for f in read_manifest(store, "r1")["files"]] == ["111"]
    assert [f["desertionNo"] for f in read_manifest(store, "r2", dt="2026-11-05")["files"]] == ["222"]


def test_manifest_records_failures_without_urls(store):
    seed_bronze(store, [row("111", "http://x/a", "http://x/b")])
    fetch = FakeFetch({"http://x/a": (None, "not_found"), "http://x/b": (image_bytes(), None)})
    rp.run_photos(store, fetch, RUN_ID, now=NOW, photo_run_id="r1")
    m = read_manifest(store, "r1")
    assert m["failed"] == [{"desertionNo": "111", "slot": 1, "status": "failed", "reason": "not_found", "attempts": 1}]
    assert m["counts"]["stored"] == 1 and m["counts"]["failed"] == 1
    assert "http://" not in json.dumps(m)


def test_empty_run_still_writes_a_manifest_and_dry_run_does_not(store):
    seed_bronze(store, [row("111")])  # 사진이 없는 공고뿐
    rp.run_photos(store, FakeFetch({}), RUN_ID, dry_run=True, now=NOW, photo_run_id="dry")
    assert read_manifest(store, "dry") is None
    rp.run_photos(store, FakeFetch({}), RUN_ID, now=NOW, photo_run_id="r1")
    m = read_manifest(store, "r1")
    assert m["status"] == "ok" and m["files"] == [] and m["counts"]["stored"] == 0


def test_aborted_upload_leaves_manifest_with_the_photos_that_made_it(tmp_path):
    store = FailingStore(tmp_path, fail_on=2)
    seed_bronze(store, [row("111", "http://x/a"), row("222", "http://x/b")])
    fetch = FakeFetch({"http://x/a": (image_bytes(), None), "http://x/b": (image_bytes(), None)})
    out = rp.run_photos(store, fetch, RUN_ID, now=NOW, photo_run_id="r1")
    m = read_manifest(store, "r1")
    assert out["aborted"] and m["status"] == "aborted"
    assert [f["desertionNo"] for f in m["files"]] == ["222"]  # 올라간 사진만 목록에 있다


def test_killed_run_keeps_manifest_and_state_in_agreement(store, monkeypatch):
    monkeypatch.setattr(rp, "CHECKPOINT_EVERY", 1)
    seed_bronze(store, [row("111", "http://x/a"), row("222", "http://x/b")])

    def fetch(url):
        if url == "http://x/a":  # 접수일이 같으면 번호가 큰 222(b) 가 먼저, 그다음 111(a) 에서 프로세스가 죽는다고 가정
            raise RuntimeError("프로세스가 죽음")
        return image_bytes(), None

    with pytest.raises(RuntimeError):
        rp.run_photos(store, fetch, RUN_ID, now=NOW, photo_run_id="r1")
    m = read_manifest(store, "r1")
    assert m["status"] == "in_progress" and [f["desertionNo"] for f in m["files"]] == ["222"]
    notices = state_of(store)["notices"]
    assert notices["222"]["slots"]["1"]["status"] == "success" and "111" not in notices
    # 기록의 success 는 모두 manifest 에 있다
    listed = {(f["desertionNo"], f["slot"]) for f in m["files"]}
    recorded = {(no, int(s)) for no, n in notices.items() for s, r in n["slots"].items() if r["status"] == "success"}
    assert recorded <= listed


def test_manifest_updated_at_moves_on_each_write_while_generated_at_stays(store, monkeypatch):
    # 정체 판단 기준: generated_at 은 실행 시작 시각 그대로, updated_at 은 manifest 를 쓸 때마다 갱신된다.
    monkeypatch.setattr(rp, "CHECKPOINT_EVERY", 1)
    seed_bronze(store, [row("111", "http://x/a"), row("222", "http://x/b")])
    seen = []

    def fetch(url):
        m = read_manifest(store, "r1")
        if m is not None:
            seen.append((m["status"], m["generated_at"], m["updated_at"]))
        return image_bytes(), None

    rp.run_photos(store, fetch, RUN_ID, now=NOW, photo_run_id="r1")
    assert seen and seen[0][0] == "in_progress"  # 두 번째 칸을 받기 전에는 첫 체크포인트의 in_progress 가 보인다
    final = read_manifest(store, "r1")
    assert final["status"] == "ok"
    assert final["generated_at"] == NOW.isoformat(timespec="seconds")
    assert all(g == final["generated_at"] for _, g, _ in seen)
    assert datetime.fromisoformat(final["updated_at"]) >= datetime.fromisoformat(final["generated_at"])
