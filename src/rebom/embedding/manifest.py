"""Bronze 사진 manifest(bronze/rescue/image_runs/.../manifest.json)에서 임베딩할 사진 목록을 만든다.

- manifest 형식: docs/data_contract.md 6절. files[] 항목 = desertionNo, slot, original, ext, bytes, sha256, serving,
  (v2) version, replaces. failed[] 는 S3 에 파일이 없으므로 임베딩 대상이 아니다.
- 같은 (desertionNo, slot) 이 여러 실행에 나오면 version 이 큰 것, 같으면 나중 실행이 이긴다.
  교체 실패(replaced_not_found)는 files 에 새 버전이 없으므로 이전 버전이 그대로 현재 사진으로 남는다.
- 이 모듈은 S3 를 읽기만 한다.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

MANIFEST_PREFIX = "bronze/rescue/image_runs/"
IMAGES_PREFIX = "bronze/rescue/images/"
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Photo:
    desertion_no: str
    slot: int
    version: int
    s3_key: str
    sha256: str
    ext: str
    bytes: int | None
    photo_run_id: str | None = None  # Bronze 사진 수집 실행. Silver 입력에는 없다 (추적은 publish_id)

    @property
    def key(self) -> tuple[str, int]:
        return (self.desertion_no, self.slot)


def parse_manifest(doc: dict[str, Any]) -> list[Photo]:
    """manifest 하나 → Photo 목록. 형식이 틀린 항목은 조용히 넘기지 않고 오류를 낸다."""
    run_id = doc.get("run_id")
    files = doc.get("files")
    if not run_id or not isinstance(files, list):
        raise ValueError("manifest 에 run_id 또는 files 가 없음")
    photos = []
    for item in files:
        no = str(item.get("desertionNo") or "").strip()
        slot = item.get("slot")
        sha = str(item.get("sha256") or "").lower()
        original = item.get("original")
        version = item.get("version", 1)
        if not no or type(slot) is not int or not 1 <= slot <= 8:
            raise ValueError(f"공고번호·슬롯 오류: {no!r} {slot!r} (run {run_id})")
        if not _SHA256.fullmatch(sha):
            raise ValueError(f"sha256 형식 오류: {no} slot {slot} (run {run_id})")
        if not isinstance(original, str) or not original.startswith(f"{IMAGES_PREFIX}{no}/"):
            raise ValueError(f"원본 경로 오류: {original!r} (run {run_id})")
        if type(version) is not int or version < 1:
            raise ValueError(f"버전 오류: {no} slot {slot} (run {run_id})")
        photos.append(
            Photo(no, slot, version, original, sha, str(item.get("ext") or ""), item.get("bytes"), run_id)
        )
    return photos


def _run_order(doc: dict[str, Any]) -> tuple[str, str]:
    return (str(doc.get("generated_at") or ""), str(doc.get("run_id") or ""))


def latest_photos(manifests: Iterable[dict[str, Any]]) -> dict[tuple[str, int], Photo]:
    """여러 실행의 manifest → (공고번호, 슬롯)별 현재 사진."""
    current: dict[tuple[str, int], Photo] = {}
    for doc in sorted(manifests, key=_run_order):
        for p in parse_manifest(doc):
            old = current.get(p.key)
            if old is None or p.version >= old.version:
                current[p.key] = p
    return current


def summarize(manifests: list[dict[str, Any]]) -> dict[str, Any]:
    """discover 출력용 요약."""
    photos = latest_photos(manifests)
    failed = Counter()
    for doc in manifests:
        for item in doc.get("failed") or []:
            failed[str(item.get("reason") or "unknown")] += 1
    exts = Counter(p.ext for p in photos.values())
    return {
        "runs": [
            {"run_id": d.get("run_id"), "status": d.get("status"), "files": len(d.get("files") or []),
             "failed": len(d.get("failed") or [])}
            for d in sorted(manifests, key=_run_order)
        ],
        "item_fields": sorted({k for d in manifests for it in (d.get("files") or [])[:1] for k in it}),
        "current_photos": len(photos),
        "notices": len({p.desertion_no for p in photos.values()}),
        "versions": dict(Counter(p.version for p in photos.values())),
        "ext": dict(exts),
        "failed_reasons": dict(failed),
    }


# --- S3 (읽기만) ---------------------------------------------------------------

def list_keys(s3: Any, bucket: str, prefix: str) -> Iterable[dict[str, Any]]:
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        yield from page.get("Contents", [])


def load_manifests(s3: Any, bucket: str, prefix: str = MANIFEST_PREFIX) -> list[dict[str, Any]]:
    docs = []
    for obj in list_keys(s3, bucket, prefix):
        if obj["Key"].endswith("/manifest.json"):
            body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
            docs.append(json.loads(body.decode("utf-8-sig")))
    return docs
