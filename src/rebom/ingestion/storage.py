"""수집 결과(bronze)와 상태 파일을 저장하는 곳. 로컬 폴더와 S3 중 하나를 쓴다.

키는 두 곳에서 같다. 로컬은 data/<키>, S3 는 <접두사><키> 이다.
  bronze/rescue/raw/dt=YYYY-MM-DD/run_id=<id>/<종류>.jsonl.gz, manifest.json   (데이터 계약 5절)
  state/rescue_state.json                                                       (마지막 성공일, 보호중 목록)

REBOM_S3_BUCKET 이 있으면 S3, 없으면 로컬 data/ 에 저장한다. AWS 자격증명은 환경(역할, 환경변수, ~/.aws)에서 읽고
이 코드와 .env 에는 두지 않는다.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path


class UploadError(RuntimeError):
    """올리기에 실패했거나 올린 내용이 맞지 않는다. 호출하는 쪽은 상태를 갱신하지 않고 끝내야 한다."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class LocalStore:
    """root 아래에 키 그대로 저장한다."""

    kind = "local"

    def __init__(self, root: Path):
        self.root = Path(root)

    def describe(self, key: str) -> str:
        return str(self.root / key)

    def exists(self, key: str) -> bool:
        return (self.root / key).exists()

    def put_file(self, local: Path, key: str, checksum: str | None = None, overwrite: bool = False) -> None:
        dest = self.root / key
        if dest.exists() and not overwrite:
            raise UploadError(f"이미 있는 경로라 덮어쓰지 않음: {key}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, dest)
        if checksum and sha256_file(dest) != checksum:
            raise UploadError(f"저장한 파일의 체크섬이 다름: {key}")

    def put_bytes(self, key: str, data: bytes, overwrite: bool = True) -> None:
        dest = self.root / key
        if dest.exists() and not overwrite:
            raise UploadError(f"이미 있는 경로라 덮어쓰지 않음: {key}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)  # 쓰다 끊겨도 이전 내용이 남게 바꿔치기

    def get_bytes(self, key: str) -> bytes | None:
        path = self.root / key
        return path.read_bytes() if path.exists() else None


def _is_not_found(exc: Exception) -> bool:
    code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
    return code in ("404", "NoSuchKey", "NotFound")


class S3Store:
    """client 는 boto3 의 s3 클라이언트(put_object, head_object, get_object 를 쓴다). 시험에서는 가짜를 넣는다."""

    kind = "s3"

    def __init__(self, client, bucket: str, prefix: str = "", retries: int = 3):
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.retries = retries

    def _key(self, key: str) -> str:
        return self.prefix + key

    def describe(self, key: str) -> str:
        return f"s3://{self.bucket}/{self._key(key)}"

    def _retry(self, what: str, fn):
        for attempt in range(1, self.retries + 1):
            try:
                return fn()
            except Exception as e:  # 오류 메시지에 자격증명이 들어갈 수 있어 종류만 남긴다
                if _is_not_found(e) or attempt == self.retries:
                    raise UploadError(f"{what} 실패({type(e).__name__})") from None
                time.sleep(2 * attempt)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except Exception as e:
            if _is_not_found(e):
                return False
            raise UploadError(f"존재 확인 실패({type(e).__name__})") from None

    def _put(self, key: str, data: bytes, checksum: str, overwrite: bool) -> None:
        if not overwrite and self.exists(key):
            raise UploadError(f"이미 있는 경로라 덮어쓰지 않음: {key}")
        self._retry(
            f"업로드 {key}",
            lambda: self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data, Metadata={"sha256": checksum}),
        )
        head = self._retry(f"확인 {key}", lambda: self.client.head_object(Bucket=self.bucket, Key=self._key(key)))
        if head.get("ContentLength") != len(data) or head.get("Metadata", {}).get("sha256") != checksum:
            raise UploadError(f"올린 파일이 원본과 다름: {key}")

    def put_file(self, local: Path, key: str, checksum: str | None = None, overwrite: bool = False) -> None:
        data = Path(local).read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if checksum and actual != checksum:
            raise UploadError(f"올리기 전 파일의 체크섬이 manifest 값과 다름: {key}")
        self._put(key, data, actual, overwrite)

    def put_bytes(self, key: str, data: bytes, overwrite: bool = True) -> None:
        self._put(key, data, hashlib.sha256(data).hexdigest(), overwrite)

    def get_bytes(self, key: str) -> bytes | None:
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"]
            return body.read()
        except Exception as e:
            if _is_not_found(e):
                return None
            raise UploadError(f"읽기 실패({type(e).__name__})") from None


def make_store(local_root: Path):
    """REBOM_S3_BUCKET 이 있으면 S3, 없으면 local_root(보통 data/)에 저장한다."""
    bucket = os.getenv("REBOM_S3_BUCKET", "").strip()
    if not bucket:
        return LocalStore(local_root)
    try:
        import boto3  # 로컬 저장만 쓸 때는 필요 없다
    except ImportError:
        raise SystemExit("REBOM_S3_BUCKET 이 있는데 boto3 가 설치돼 있지 않습니다.") from None
    return S3Store(boto3.client("s3"), bucket, os.getenv("REBOM_S3_PREFIX", ""))
