"""임베딩할 '현재 사진'을 어디서 읽을지 고른다: Silver READY 발행본(--publish-id), bronze(지금 기본), silver(점검용).

- bronze: 사진 manifest(bronze/rescue/image_runs/.../manifest.json)를 모두 읽어 칸마다 최신 버전을 고른다 (manifest.py).
- READY 발행본: 발행본이 고정한 공고·현재 사진 스냅샷을 읽는다. 최신 표로 바꿔 읽지 않는다(글 임베딩과 같음).
  보호중(status_group = PROTECTING) 공고의 사진만 대상이다. 추적 키는 publish_id(= silver_ + bronze_run_id)이고
  gold_run_input 에 실행마다 한 행으로 남긴다.
- silver: Silver 이미지 표(칸마다 최신 정상 버전만 둠)의 desertion_no·image_slot·original_s3_key·sha256 을 읽는다.
  사진 파일은 모두 Bronze S3 원본(original_s3_key)에서 읽는다. 공공 API 주소(source_url)는 쓰지 않는다.
- Silver 이미지 표에는 버전·사진 실행 칸이 없다. 버전은 키 이름(popfile<N>_v002.jpg → 2, 없으면 1)에서 읽고,
  photo_run_id 는 비운다(Bronze·Silver 안에서만 쓰는 값).
- --source silver 는 스냅샷을 고정하지 않으므로, silver test 로는 S3·RDS 에 쓰는 명령을 막는다(cli.py). 비교·점검(--dry-run, staged, compare-sources)만 된다.
- 이 모듈은 S3·Glue 를 읽기만 한다.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable

from rebom.common.silver_input import PublishedInput
from rebom.embedding import manifest as mf

SOURCES = ("bronze", "silver")
DEFAULT_SOURCE = "bronze"
SILVER_FIELDS = ("desertion_no", "image_slot", "original_s3_key", "sha256")
_KEY = re.compile(r"popfile(?P<slot>[1-8])(?:_v(?P<version>\d{3}))?\.(?P<ext>[A-Za-z0-9]+)$")

Current = dict[tuple[str, int], mf.Photo]


def photos_from_silver(rows: Iterable[dict[str, Any]], env_name: str) -> Current:
    """Silver 이미지 표 행 → (공고번호, 칸)별 현재 사진. 키·sha256 이 없는 행(사진을 아직 못 받은 칸)은 뺀다.

    형식이 틀린 행은 조용히 넘기지 않고 오류를 낸다 (manifest.parse_manifest 와 같은 기준)."""
    current: Current = {}
    for r in rows:
        key, sha = r.get("original_s3_key"), r.get("sha256")
        if not key or not sha:
            continue
        no = str(r.get("desertion_no") or "").strip()
        slot = r.get("image_slot")
        sha = str(sha).lower()
        if not no or slot is None or not 1 <= int(slot) <= 8:
            raise ValueError(f"공고번호·칸 오류: {no!r} {slot!r} (silver {env_name})")
        slot = int(slot)
        if not mf._SHA256.fullmatch(sha):
            raise ValueError(f"sha256 형식 오류: {no} slot {slot} (silver {env_name})")
        m = _KEY.search(key)
        if not key.startswith(f"{mf.IMAGES_PREFIX}{no}/") or m is None or int(m["slot"]) != slot:
            raise ValueError(f"원본 경로 오류: {key!r} (silver {env_name})")
        if (no, slot) in current:
            raise ValueError(f"같은 칸이 두 번 있음: {no} slot {slot} (silver {env_name})")
        version = int(m["version"]) if m["version"] else 1
        current[(no, slot)] = mf.Photo(no, slot, version, key, sha, m["ext"].lower(), None)
    return current


def load_silver(env_name: str, profile: str | None = None) -> Current:
    """Glue 의 Silver 이미지 표를 읽는다. pyiceberg 는 embed extra 에 있다."""
    from rebom.storage.silver.aws_catalog import get_aws_catalog
    from rebom.storage.silver.environment_config import get_silver_environment

    env = get_silver_environment(env_name)  # prod 는 운영 표가 정해질 때까지 PROD_DISABLED 오류
    table = get_aws_catalog(profile=profile).load_table(env.image_table)
    rows = table.scan(selected_fields=SILVER_FIELDS).to_arrow().to_pylist()
    return photos_from_silver(rows, env.name)


def load_published(catalog: Any, published: PublishedInput) -> tuple[Current, set[str]]:
    """READY 발행본의 (보호중 공고의 현재 사진, 보호중 공고번호). 두 표 모두 발행본이 고정한 스냅샷으로 읽는다."""
    from pyiceberg.expressions import EqualTo

    published.validate_snapshots(catalog)
    notices = catalog.load_table(published.notice_table).scan(
        row_filter=EqualTo("status_group", "PROTECTING"), selected_fields=("desertion_no",),
        snapshot_id=published.notice_snapshot_id,
    ).to_arrow().column("desertion_no").to_pylist()
    protected = {str(no) for no in notices}
    rows = catalog.load_table(published.photo_table).scan(
        selected_fields=SILVER_FIELDS, snapshot_id=published.photo_snapshot_id,
    ).to_arrow().to_pylist()
    current = {k: p for k, p in photos_from_silver(rows, published.publish_id).items() if k[0] in protected}
    if not current:  # 잘못된 스냅샷으로 "보낼 사진 없음" 처리되지 않게
        raise ValueError(f"보호중 공고의 사진이 없음: {published.publish_id}")
    return current, protected


def load_current(source: str, s3: Any, bucket: str, silver_env: str = "test") -> tuple[Current, list[dict] | None]:
    """(현재 사진, manifest 목록). silver 면 manifest 목록은 None."""
    if source == "bronze":
        manifests = mf.load_manifests(s3, bucket)
        return mf.latest_photos(manifests), manifests
    if source == "silver":
        return load_silver(silver_env), None
    raise ValueError(f"source 는 {SOURCES} 중 하나")


def compare(bronze: Current, silver: Current, examples: int = 5) -> dict[str, Any]:
    """두 기준의 현재 사진 대조. 전환 조건: only_bronze·only_silver·sha_diff·key_diff 가 모두 0."""
    both = bronze.keys() & silver.keys()
    only_b = sorted(bronze.keys() - silver.keys())
    only_s = sorted(silver.keys() - bronze.keys())
    sha_diff = sorted(k for k in both if bronze[k].sha256 != silver[k].sha256)
    key_diff = sorted(k for k in both if bronze[k].s3_key != silver[k].s3_key)
    return {
        "bronze_photos": len(bronze),
        "silver_photos": len(silver),
        "silver_coverage_pct": round(100 * len(both) / len(bronze), 1) if bronze else None,
        "same": len(both) - len(set(sha_diff) | set(key_diff)),
        "only_bronze": len(only_b),
        "only_silver": len(only_s),
        "sha_diff": len(sha_diff),
        "key_diff": len(key_diff),
        "only_bronze_versions": dict(Counter(bronze[k].version for k in only_b)),
        "only_bronze_by_run": dict(Counter(bronze[k].photo_run_id for k in only_b).most_common(examples)),
        "diff_examples": [
            {"desertion_no": k[0], "slot": k[1], "bronze_version": bronze[k].version,
             "silver_version": silver[k].version} for k in (sha_diff or key_diff)[:examples]
        ],
        "switch_ready": bool(bronze) and not (only_b or only_s or sha_diff or key_diff),
    }
