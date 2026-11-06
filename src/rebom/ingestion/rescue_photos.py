"""구조동물 사진 수집. 보호중 공고 중 새 공고의 사진을 있는 만큼 전부(popfile1~8) 받아 S3(또는 로컬 data/)에 저장한다.
임베딩에 몇 장을 쓸지는 이 모듈이 정하지 않는다(서동현 님 쪽). 슬롯은 popfile 번호 그대로라서 1, 7, 8번 칸만 채워진 공고도 그 번호대로 저장된다.

저장은 두 갈래이고 경로로 구분한다.
  원본      bronze/rescue/images/<desertionNo>/popfile<N>.<확장자>   받은 그대로. 확장자는 파일 내용으로 판단(jpg, png ...)
  서비스용  images/<desertionNo>/<N>.webp, <N>_thumb.webp            풀 해상도 WebP와 가로 400px 썸네일 (서비스 이미지 서빙 설계, 11/3)
  서비스용은 원본에서 만든 파생물이라 bronze/ 아래에 두지 않는다. 경로는 상수로 모아 두었고, 이민혁·이광훈 님과 맞춘 뒤 바뀔 수 있다.

기록 (state/rescue_photos_state.json): 공고별·칸별로 상태(success, failed, gave_up), 시도 횟수, 실패 사유를 남긴다.
  - 새 사진 = 주소가 있는데 기록이 없는 칸. 첫 실행은 지금 보호중인 공고 전체를 받는다(--limit 으로 나눠 받을 수 있다).
  - 주소가 없는 칸은 기록하지 않는다(8칸이면 기록이 불어나기 때문). 사진이 한 장도 없는 공고만 공고 단위로 no_photos 를 표시한다.
  - failed 는 다음 실행에서 다시 시도하고, MAX_ATTEMPTS 번째 실패면 gave_up 으로 두고 다시 시도하지 않는다.
  - 이미 받은 공고의 사진이 바뀌거나, 나중에 다른 칸에 사진이 생기는 경우는 다루지 않는다(기록이 없는 칸이 생기면 새 사진으로 받기는 한다).
  - 업로드는 일정 개수마다 기록을 저장(checkpoint)한다. 기록에는 올라간 것만 반영되므로 중간에 끊겨도 이어서 받을 수 있다.

인계 (bronze/rescue/image_runs/dt=<실행일>/run_id=<사진 실행 id>/manifest.json): 실행마다 이번에 새로 저장한 사진의 목록(공고 번호, 칸,
  원본 경로, 확장자, 크기, sha256, 서비스용 경로)과 실패 목록을 남긴다. Silver 가 이것을 읽어 사진 레코드의 위치 칸을 채운다.
  증분 수집 manifest 와 달리 "맨 마지막에 한 번"이 아니라 checkpoint 마다 덮어쓴다(status: in_progress -> ok 또는 aborted).
  기록(state)을 저장하기 직전에 항상 manifest 를 먼저 쓰므로, 기록에 success 로 남은 사진은 반드시 어느 manifest 에든 들어 있다.
  중간에 프로세스가 죽으면 in_progress 인 채로 남는데, 거기 적힌 사진은 실제로 저장된 것이다. 한 칸이 성공하면 한 번만 목록에 오르므로, 정상 흐름에서는 같은 (번호, 칸)이 여러 manifest 에 나오지 않는다.

입력은 증분 수집(rescue_incremental)이 저장한 Bronze 실행의 list_notice, list_protect 이다(공고중 + 보호중 = 지금 보호 중인 공고).
사진 주소는 http:// 이고 인증키가 필요 없다. 로그에는 건수만 남기고 주소와 키는 남기지 않는다.

사용 예 (레포 루트에서):
  PYTHONPATH=src python -m rebom.ingestion.rescue_photos --dry-run          # 받을 대상 건수만 보고 아무것도 받거나 쓰지 않음
  PYTHONPATH=src python -m rebom.ingestion.rescue_photos --limit 20          # 20장만 받아 시험
  PYTHONPATH=src python -m rebom.ingestion.rescue_photos                     # 마지막 수집 실행(state/rescue_state.json)을 처리

저장 위치: 환경변수 REBOM_S3_BUCKET 이 있으면 S3, 없으면 로컬 data/ (rebom.ingestion.storage).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from rebom.ingestion.storage import UploadError, make_store

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data"
RAW_PREFIX = "bronze/rescue/raw"
RESCUE_STATE_KEY = "state/rescue_state.json"
STATE_KEY = "state/rescue_photos_state.json"
ORIGINAL_PREFIX = "bronze/rescue/images"  # 받은 그대로
SERVING_PREFIX = "images"  # 서비스용 파생물(WebP, 썸네일). CloudFront /images/* 가 가리키는 곳
MANIFEST_PREFIX = "bronze/rescue/image_runs"  # 실행별 인계 목록. 사진 폴더(images/<번호>)와 섞이지 않게 이름을 다르게 둔다
CODE_VERSION = "2026-11-04"
LIST_KINDS = ("list_notice", "list_protect")
SLOTS = tuple(range(1, 9))  # popfile1~8 중 주소가 있는 칸을 모두 받는다 (11/4 결정, 이광훈 님 Silver 도 슬롯 1~8 을 전제)
MAX_ATTEMPTS = 3  # 같은 칸을 이 횟수까지 시도하고 넘으면 gave_up
THUMB_WIDTH = 400
WEBP_QUALITY = 85
MAX_BYTES = 30 * 1024 * 1024  # 이보다 큰 응답은 사진으로 보지 않는다
HTTP_TIMEOUT = (5, 30)  # 연결, 읽기 (초)
CHECKPOINT_EVERY = 100
KST = timezone(timedelta(hours=9))
SAFE_ID = re.compile(r"^[0-9A-Za-z_-]{1,40}$")  # 공고 번호가 경로에 들어가므로 이상한 문자는 걸러낸다

# 실패 사유
NOT_FOUND, TIMEOUT, NETWORK, SERVER_ERROR, HTTP_ERROR, BROKEN, CONVERT, UNSUPPORTED = (
    "not_found", "timeout", "network_error", "server_error", "http_error", "broken_file", "convert_error", "unsupported_format",
)
# MPO 는 휴대폰이 여러 장(깊이 정보 등)을 한 파일에 담은 JPEG 이다. 파일은 .jpg 로 열리지만 Pillow 는 format 을 "MPO" 로 알려 준다.
FORMAT_EXT = {"JPEG": "jpg", "MPO": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp", "BMP": "bmp", "TIFF": "tif"}


def log(msg: str) -> None:
    print(msg, flush=True)


def original_key(no: str, slot: int, ext: str) -> str:
    return f"{ORIGINAL_PREFIX}/{no}/popfile{slot}.{ext}"


def serving_key(no: str, slot: int, thumb: bool = False) -> str:
    return f"{SERVING_PREFIX}/{no}/{slot}{'_thumb' if thumb else ''}.webp"


# ---------- 내려받기 ----------

def http_fetch(url: str) -> tuple[bytes | None, str | None]:
    """(바이트, None) 또는 (None, 실패 사유). 주소는 로그와 예외에 남기지 않는다."""
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT, stream=True)
    except requests.Timeout:
        return None, TIMEOUT
    except requests.RequestException:
        return None, NETWORK
    try:
        if resp.status_code in (404, 410):
            return None, NOT_FOUND
        if resp.status_code >= 500:
            return None, SERVER_ERROR
        if resp.status_code != 200:
            return None, HTTP_ERROR
        buf = bytearray()
        for chunk in resp.iter_content(65536):
            buf.extend(chunk)
            if len(buf) > MAX_BYTES:
                return None, BROKEN
        return bytes(buf), None
    except requests.Timeout:
        return None, TIMEOUT
    except requests.RequestException:
        return None, NETWORK  # 받는 도중 끊김 포함
    finally:
        resp.close()


# ---------- 사진 검사와 변환 ----------

class BrokenImage(Exception):
    pass


class UnsupportedImage(Exception):
    """사진으로는 읽히지만 FORMAT_EXT 에 없는 형식. 깨진 파일과 구분해 사유를 남긴다."""


def open_image(data: bytes):
    """사진으로 끝까지 읽히는지 확인한다(끊긴 파일, 오류 페이지, 0바이트는 BrokenImage). (확장자, 이미지)를 돌려준다."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        img.load()  # 일부만 받은 파일은 여기서 오류가 난다
    except Exception:
        raise BrokenImage() from None
    ext = FORMAT_EXT.get(fmt or "")
    if ext is None:
        raise UnsupportedImage(fmt)
    return ext, img


def make_webp(img) -> tuple[bytes, bytes]:
    """(풀 해상도 WebP, 가로 400px 썸네일 WebP). 작은 사진은 키우지 않는다."""
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(img)  # 휴대폰 사진의 회전 정보를 반영
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if has_alpha else "RGB")

    def encode(image) -> bytes:
        out = io.BytesIO()
        image.save(out, format="WEBP", quality=WEBP_QUALITY)
        return out.getvalue()

    full = encode(img)
    thumb = img.copy()
    thumb.thumbnail((THUMB_WIDTH, 10_000), Image.LANCZOS)  # 가로 기준, 비율 유지, 확대 없음
    return full, encode(thumb)


# ---------- 기록과 입력 ----------

def load_state(store) -> dict:
    data = store.get_bytes(STATE_KEY)
    return json.loads(data.decode("utf-8")) if data else {"version": 1, "notices": {}}


def save_state(store, state: dict) -> None:
    store.put_bytes(STATE_KEY, json.dumps(state, ensure_ascii=False).encode("utf-8"), overwrite=True)


def load_bronze_rows(store, run_id: str) -> dict[str, dict]:
    """수집 실행 한 건의 공고중·보호중 목록을 읽어 {공고번호: 항목} 으로 돌려준다. manifest 와 체크섬이 맞지 않으면 멈춘다."""
    if not re.fullmatch(r"\d{8}T\d{6}-[0-9a-f]+", run_id):
        raise SystemExit("run_id 모양이 맞지 않음")
    prefix = f"{RAW_PREFIX}/dt={run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}/run_id={run_id}"
    raw_manifest = store.get_bytes(f"{prefix}/manifest.json")
    if raw_manifest is None:
        raise SystemExit("수집 실행의 manifest 가 없음. 수집이 끝까지 올라가지 않았을 수 있음")
    manifest = json.loads(raw_manifest.decode("utf-8"))
    if manifest.get("status") != "ok":
        raise SystemExit(f"수집 실행의 상태가 ok 가 아님({manifest.get('status')})")
    checksums = {f["file"]: f["checksum"] for f in manifest.get("files", [])}
    rows: dict[str, dict] = {}
    for kind in LIST_KINDS:
        name = f"{kind}.jsonl.gz"
        raw = store.get_bytes(f"{prefix}/{name}")
        if raw is None:
            raise SystemExit(f"{name} 가 없음")
        if hashlib.sha256(raw).hexdigest() != checksums.get(name):
            raise SystemExit(f"{name} 의 체크섬이 manifest 와 다름")
        for line in gzip.decompress(raw).decode("utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                no = row.get("desertionNo")
                if no:
                    rows.setdefault(str(no), row)
    return rows


def plan_work(rows: dict[str, dict], state: dict, now: datetime) -> tuple[list[dict], dict]:
    """받을 칸 목록과 요약. 사진이 한 장도 없는 공고만 state 에 no_photos 로 표시한다(state 를 고친다). 주소가 없는 칸은 기록하지 않는다."""
    work: list[dict] = []
    summary = Counter()
    order = sorted(rows, key=lambda n: (str(rows[n].get("happenDt") or ""), n), reverse=True)  # 최근 접수일 먼저
    for no in order:
        if not SAFE_ID.match(no):
            summary["skipped_bad_id"] += 1
            continue
        notice = state["notices"].get(no, {})
        slots = notice.get("slots", {})
        any_url = False
        for slot in SLOTS:
            rec = slots.get(str(slot))
            url = rows[no].get(f"popfile{slot}")
            url = url.strip() if isinstance(url, str) else ""
            any_url = any_url or bool(url)
            if rec is None:
                if url:  # 주소가 없는 칸은 기록하지 않는다
                    work.append({"no": no, "slot": slot, "url": url, "attempts": 0})
                    summary["new"] += 1
            elif rec["status"] == "failed":
                work.append({"no": no, "slot": slot, "url": url or rec.get("source_url", ""), "attempts": rec["attempts"]})
                summary["retry"] += 1
        if not any_url and not slots and not notice.get("no_photos"):
            state["notices"].setdefault(no, {"slots": {}})["no_photos"] = True  # 사진이 한 장도 없는 공고는 공고 단위로 한 번만 표시
            summary["no_photos"] += 1
    return work, dict(summary)


# ---------- 한 칸 처리 ----------

def process_slot(store, fetch, item: dict, now: datetime) -> dict:
    """기록할 내용을 돌려준다. 업로드 실패(UploadError)는 그대로 올려 보낸다."""
    no, slot, url, before = item["no"], item["slot"], item["url"], item["attempts"]
    attempts = before + 1

    def failure(reason: str) -> dict:
        status = "gave_up" if attempts >= MAX_ATTEMPTS else "failed"
        return {"status": status, "attempts": attempts, "reason": reason, "source_url": url, "last_attempt_at": now.isoformat(timespec="seconds")}

    data, reason = fetch(url)
    if reason:
        return failure(reason)
    try:
        ext, img = open_image(data)
    except BrokenImage:
        return failure(BROKEN)
    except UnsupportedImage:
        return failure(UNSUPPORTED)
    try:
        full, thumb = make_webp(img)
    except Exception:
        return failure(CONVERT)
    # 기록이 이 저장의 기준이므로 같은 키가 이미 있어도(이전 실행이 기록 직전에 끊긴 경우) 덮어써서 다시 맞춘다.
    store.put_bytes(original_key(no, slot, ext), data, overwrite=True)
    store.put_bytes(serving_key(no, slot), full, overwrite=True)
    store.put_bytes(serving_key(no, slot, thumb=True), thumb, overwrite=True)
    return {
        "status": "success", "attempts": attempts, "reason": None, "ext": ext, "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(), "source_url": url, "stored_at": now.isoformat(timespec="seconds"),
    }


def manifest_entry(item: dict, rec: dict) -> dict:
    no, slot = item["no"], item["slot"]
    return {
        "desertionNo": no, "slot": slot, "original": original_key(no, slot, rec["ext"]), "ext": rec["ext"],
        "bytes": rec["bytes"], "sha256": rec["sha256"],
        "serving": {"image": serving_key(no, slot), "thumb": serving_key(no, slot, thumb=True)},
    }


def write_manifest(store, key: str, photo_run_id: str, bronze_run_id: str, now: datetime, status: str, summary: dict, stored: list, failed: list, updated_at: datetime | None = None) -> None:
    # generated_at 은 실행 시작 시각이고 바뀌지 않는다. updated_at 은 manifest 를 쓸 때마다 갱신돼, in_progress 가 멈췄는지 판단하는 기준이 된다.
    manifest = {
        "source": "rescue_photos", "run_id": photo_run_id, "bronze_run_id": bronze_run_id,
        "generated_at": now.isoformat(timespec="seconds"), "updated_at": (updated_at or now).isoformat(timespec="seconds"),
        "code_version": CODE_VERSION, "status": status,
        "counts": {"stored": len(stored), "failed": len(failed), **summary},
        "files": stored,  # 이번 실행에서 새로 저장한 사진
        "failed": failed,  # 이번 실행에서 실패한 칸 (failed 는 다음 실행이 다시 시도, gave_up 은 포기)
    }
    store.put_bytes(key, json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"), overwrite=True)


def run_photos(store, fetch, run_id: str, limit: int | None = None, delay: float = 0.0, dry_run: bool = False, now: datetime | None = None, photo_run_id: str | None = None) -> dict:
    now = now or datetime.now(KST)
    rows = load_bronze_rows(store, run_id)
    state = load_state(store)
    work, summary = plan_work(rows, state, now)
    summary["bronze_notices"] = len(rows)
    summary["to_process"] = len(work)
    if limit is not None:
        work = work[:limit]
    summary["this_run"] = len(work)
    if dry_run:
        return {**summary, "dry_run": True}

    photo_run_id = photo_run_id or f"{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    manifest_key = f"{MANIFEST_PREFIX}/dt={now:%Y-%m-%d}/run_id={photo_run_id}/manifest.json"
    stored: list[dict] = []
    failed: list[dict] = []
    results = Counter()
    reasons = Counter()
    aborted = None
    done = 0

    def checkpoint(status: str) -> None:
        # manifest 를 먼저 쓰고 기록을 저장한다. 기록에 success 로 남은 사진이 어느 manifest 에도 없는 일이 없게 하려는 순서다.
        write_manifest(store, manifest_key, photo_run_id, run_id, now, status, summary, stored, failed, updated_at=datetime.now(KST))
        state["last_bronze_run_id"] = run_id
        state["updated_at"] = now.isoformat(timespec="seconds")
        save_state(store, state)

    try:
        for item in work:
            rec = process_slot(store, fetch, item, now)
            state["notices"].setdefault(item["no"], {"slots": {}})["slots"][str(item["slot"])] = rec
            results[rec["status"]] += 1
            if rec["status"] == "success":
                stored.append(manifest_entry(item, rec))
            else:
                reasons[rec["reason"]] += 1
                failed.append({"desertionNo": item["no"], "slot": item["slot"], "status": rec["status"], "reason": rec["reason"], "attempts": rec["attempts"]})
            done += 1
            if done % CHECKPOINT_EVERY == 0:
                checkpoint("in_progress")
                log(f"  진행 {done}/{len(work)}")
            if delay:
                time.sleep(delay)
    except UploadError as e:
        aborted = str(e)
    checkpoint("aborted" if aborted else "ok")  # 끝났거나 끊겼거나, 올라간 것까지는 기록한다
    out = {**summary, "results": dict(results), "reasons": dict(reasons), "processed": done, "manifest": manifest_key}
    if aborted:
        out["aborted"] = aborted
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", help="처리할 수집 실행 (기본: state/rescue_state.json 의 last_run_id)")
    ap.add_argument("--limit", type=int, help="이번에 받을 최대 칸 수(시험이나 나눠 받기용)")
    ap.add_argument("--delay", type=float, default=0.2, help="사진 사이에 쉬는 초 (공공 서버 부담을 줄이기 위함)")
    ap.add_argument("--dry-run", action="store_true", help="대상 건수만 계산하고 받거나 쓰지 않는다")
    args = ap.parse_args(argv)

    store = make_store(DATA_DIR)
    run_id = args.run_id
    if not run_id:
        raw = store.get_bytes(RESCUE_STATE_KEY)
        run_id = json.loads(raw.decode("utf-8")).get("last_run_id") if raw else None
    if not run_id:
        sys.exit("처리할 수집 실행이 없음. 먼저 rescue_incremental 을 실행하거나 --run-id 를 주세요.")
    log(f"사진 수집 · 수집 실행 {run_id}{' · dry-run' if args.dry_run else ''}")
    out = run_photos(store, http_fetch, run_id, limit=args.limit, delay=args.delay, dry_run=args.dry_run)
    for k, v in out.items():
        log(f"  {k}: {v}")
    if out.get("aborted"):
        sys.exit(f"업로드 실패로 중간에 끝남(올라간 것까지는 기록함): {out['aborted']}")


if __name__ == "__main__":
    try:
        main()
    except UploadError as e:
        sys.exit(f"업로드 실패: {e}")
