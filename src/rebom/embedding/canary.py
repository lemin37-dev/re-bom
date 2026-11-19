"""매일 임베딩 전 모델 변화 감시 (고정 사진 6장).

모델 이름(gemini-embedding-2)이 같아도 제공자 쪽에서 내부가 바뀌면 새 벡터와 기존 벡터가 섞여 검색이 망가진다.
매일 embed 전에 고정 사진 6장을 다시 임베딩해 기준 벡터와 코사인을 비교한다.

- 모두 THRESHOLD(0.99) 이상: 통과. 결과를 checks/ 에 남기고 DAG 가 그대로 진행한다.
- 하나라도 미만: 멈춘다(종료 코드 3 → Airflow task 실패 → embed·load 가 돌지 않음).
  이번 벡터를 모델 이름·날짜 경로(drift/date=…/)에 저장하고, 로그에 대응 방법을 출력한다.

기준(reference/)은 처음 실행할 때 S3 에 이미 있는 벡터(Google 직접 호출로 만든 것)에서 자동으로 만든다.
전처리 결과 JPEG 도 같이 저장해 Bronze 사진이나 PIL 버전이 바뀌어도 같은 바이트를 보낸다.
S3 경로는 gold/image_embedding/ 밖에 둔다(load_staged 가 그 아래 파일을 모두 읽기 때문).
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from rebom.embedding import prep, staging
from rebom.embedding.manifest import list_keys

CANARY_SIZE = 6  # 요청 1번(IMAGE_BATCH)
THRESHOLD = 0.99
ROOT = "gold/image_embedding_canary/"
EXIT_DRIFT = 3


def prefix(model: str, dim: int) -> str:
    return f"{ROOT}model={model}/dim={dim}/"


def _exists(s3, bucket: str, key: str) -> bool:
    return any(o["Key"] == key for o in list_keys(s3, bucket, key))


def _cosine(a, b) -> float:
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def _now() -> tuple[str, str, str]:
    t = time.gmtime()
    return time.strftime("%Y-%m-%d", t), time.strftime("%H%M%S", t), time.strftime("%Y-%m-%dT%H:%M:%SZ", t)


def build_reference(s3, bucket: str, model: str, dim: int) -> dict[str, Any]:
    """S3 의 기존 벡터 중 API 로 만든 행을 sha256 순으로 골라 6장. Bronze 원본 sha256 이 맞고 빈 사진이 아닌 것만."""
    st = staging.load_staged(s3, bucket, model, dim, prep.PREP_VERSION)
    rows = sorted({r["sha256"]: r for r in st.rows.values() if r["source"] == "api"}.values(),
                  key=lambda r: r["sha256"])
    base = prefix(model, dim)
    _, _, at = _now()
    photos = []
    for r in rows:
        data = s3.get_object(Bucket=bucket, Key=r["s3_key"])["Body"].read()
        if hashlib.sha256(data).hexdigest() != r["sha256"] or prep.is_blank(data):
            continue
        jpeg_key = f"{base}reference/{r['sha256']}.jpg"
        s3.put_object(Bucket=bucket, Key=jpeg_key, Body=prep.to_jpeg(data))
        photos.append({"sha256": r["sha256"], "s3_key": r["s3_key"], "jpeg_key": jpeg_key,
                       "embed_run_id": r["embed_run_id"], "embedding": [float(x) for x in r["embedding"]]})
        if len(photos) == CANARY_SIZE:
            break
    if len(photos) < CANARY_SIZE:
        raise RuntimeError(f"기준 사진을 {CANARY_SIZE}장 고르지 못했습니다 ({len(photos)}장). embed 를 먼저 실행해 주세요.")
    ref = {"model": model, "dim": dim, "prep_version": prep.PREP_VERSION, "created_at": at,
           "threshold": THRESHOLD, "photos": photos}
    s3.put_object(Bucket=bucket, Key=f"{base}reference/reference.json",
                  Body=json.dumps(ref, ensure_ascii=False).encode("utf-8"))
    return ref


def load_reference(s3, bucket: str, model: str, dim: int) -> dict[str, Any] | None:
    key = f"{prefix(model, dim)}reference/reference.json"
    if not _exists(s3, bucket, key):
        return None
    return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())


def archive_reference(s3, bucket: str, model: str, dim: int) -> str | None:
    """기준을 다시 만들기 전에 지금 기준을 reference_archive/<시각>/ 로 옮겨 둔다(지우지 않음)."""
    ref = load_reference(s3, bucket, model, dim)
    if ref is None:
        return None
    day, hms, _ = _now()
    key = f"{prefix(model, dim)}reference_archive/{day}T{hms}/reference.json"
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(ref, ensure_ascii=False).encode("utf-8"))
    return key


@dataclass
class Result:
    ok: bool
    cosines: dict[str, float]
    created_reference: bool
    check_key: str
    drift_key: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def min_cosine(self) -> float:
        return min(self.cosines.values()) if self.cosines else 0.0


def run(s3, bucket: str, embedder, model: str, dim: int, threshold: float = THRESHOLD) -> Result:
    created = False
    ref = load_reference(s3, bucket, model, dim)
    if ref is None:
        ref = build_reference(s3, bucket, model, dim)
        created = True
    photos = ref["photos"]
    jpegs = [s3.get_object(Bucket=bucket, Key=p["jpeg_key"])["Body"].read() for p in photos]
    fresh = [prep.l2_normalize(v) for v in embedder.embed_images(jpegs)]
    if len(fresh) != len(photos) or any(len(v) != dim for v in fresh):
        raise RuntimeError(f"벡터 수·차원이 다름: 사진 {len(photos)}, 벡터 {[len(v) for v in fresh]}")
    cosines = {p["sha256"]: round(_cosine(v, p["embedding"]), 6) for p, v in zip(photos, fresh)}
    ok = min(cosines.values()) >= threshold
    usage = embedder.usage() if hasattr(embedder, "usage") else {}

    day, hms, at = _now()
    base = prefix(model, dim)
    check = {"checked_at": at, "ok": ok, "threshold": threshold, "min_cosine": min(cosines.values()),
             "cosines": cosines, "reference_created_at": ref["created_at"], "created_reference": created,
             "api_usage": usage}
    check_key = f"{base}checks/date={day}/{hms}.json"
    s3.put_object(Bucket=bucket, Key=check_key, Body=json.dumps(check, ensure_ascii=False, indent=2).encode("utf-8"))

    drift_key = None
    if not ok:  # 이번 벡터를 모델 이름·날짜로 남긴다. 사람이 비교·판단할 자료
        drift_key = f"{base}drift/date={day}/{hms}.json"
        drift = {**check, "model": model, "dim": dim, "prep_version": prep.PREP_VERSION,
                 "vectors": {p["sha256"]: v for p, v in zip(photos, fresh)}}
        s3.put_object(Bucket=bucket, Key=drift_key, Body=json.dumps(drift, ensure_ascii=False).encode("utf-8"))
    return Result(ok, cosines, created, check_key, drift_key, usage)


def drift_message(res: Result, bucket: str, model: str, dim: int, threshold: float = THRESHOLD) -> str:
    """Airflow 로그에 남길 안내. 무엇이 일어났고, 무엇을 저장했고, 사람이 무엇을 하면 되는지."""
    low = {k[:12]: v for k, v in res.cosines.items() if v < threshold}
    return "\n".join([
        "!! 임베딩 벡터값이 변경되었습니다. 모델 버전 확인이 필요합니다.",
        f"   모델 {model} ({dim}차원): 고정 사진 {len(res.cosines)}장 중 {len(low)}장이 기준({threshold}) 미만, "
        f"최소 코사인 {res.min_cosine:.6f}",
        f"   기준 미만: {low}",
        f"   응답 경로·제공자: {res.usage}",
        f"   현재 벡터를 모델 이름·날짜로 저장했습니다: s3://{bucket}/{res.drift_key}",
        "   오늘 embed·load 는 실행하지 않습니다 (기존 S3·RDS 벡터는 그대로, 새 사진 벡터만 늦어짐).",
        "",
        "   대응 방법:",
        "   1) 일시 현상인지 확인: Airflow 에서 이 task 를 다시 실행(Clear). 통과하면 그대로 진행됩니다.",
        "   2) 제공자 차이인지 확인: drift 파일의 api_usage.providers 와 이전 checks/ 의 제공자를 비교합니다.",
        "      특정 제공자만 다르면 gemini.py 에서 OpenRouter provider 를 고정(order + allow_fallbacks=false)합니다.",
        "   3) 모델이 실제로 바뀌었으면: 새 모델 키로 전체 사진을 다시 임베딩(약 $4, 승인 필요)하고, 검색 품질을 비교한 뒤",
        "      검색 API 를 전환합니다. 다시 임베딩이 끝나면 `canary --reset` 으로 기준을 새 벡터로 바꿉니다(이전 기준은 보관).",
        "   4) 확인 전까지 이 task 를 '성공'으로 표시하지 마세요. 표시하면 바뀐 벡터가 기존 벡터와 섞입니다.",
    ])
