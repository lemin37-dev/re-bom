"""임베딩 결과를 RDS 에 넣기 전에 S3 parquet 로 먼저 둔다 (S3 parquet → 확인 → 승인 뒤 RDS).

경로: s3://<버킷>/gold/image_embedding/model=<모델>/dim=<차원>/run_id=<id>/
    part-00000.parquet ...  사진 칸 1개 = 1행. 같은 sha256 은 벡터 1개를 여러 행이 나눠 쓴다
    summary.json            실행 결과 (건수·시간·건너뛴 사진의 sha256 과 사유)
"""
from __future__ import annotations

import io
import json
from array import array
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from rebom.embedding.manifest import Photo

ROOT = "gold/image_embedding/"

COLUMNS = ["desertion_no", "image_slot", "photo_version", "sha256", "s3_key",
           "embedding", "source", "embed_run_id", "prep_version", "embedded_at"]


def run_prefix(model: str, dim: int, run_id: str | None = None) -> str:
    base = f"{ROOT}model={model}/dim={dim}/"
    return base if run_id is None else f"{base}run_id={run_id}/"


def _schema(dim: int):
    import pyarrow as pa

    return pa.schema([
        ("desertion_no", pa.string()), ("image_slot", pa.int16()), ("photo_version", pa.int16()),
        ("sha256", pa.string()), ("s3_key", pa.string()),
        ("embedding", pa.list_(pa.float32(), dim)),
        ("source", pa.string()),  # api: 이번에 Gemini 호출 / reuse: 같은 sha256 벡터 재사용
        ("embed_run_id", pa.string()), ("prep_version", pa.string()), ("embedded_at", pa.string()),
    ])


def to_parquet(rows: Sequence[dict[str, Any]], dim: int) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(list(rows), schema=_schema(dim))
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    return buf.getvalue()


def read_parquet(data: bytes) -> list[dict[str, Any]]:
    """행 목록. embedding 은 float32 array 로 둔다: 파이썬 float 리스트(값당 약 32바이트)보다 8배 작아
    매일 Pod 가 전체 결과(4만+ 행 × 768)를 읽어도 메모리가 수백 MB 안에 든다. 값은 parquet 의 float32 그대로."""
    import pyarrow.parquet as pq

    table = pq.read_table(io.BytesIO(data))
    col = table.column("embedding").combine_chunks()
    dim = col.type.list_size
    flat = col.flatten()  # 오프셋 반영된 float32 값
    values = array("f")
    raw = flat.buffers()[1]
    values.frombytes(raw.to_pybytes()[flat.offset * 4:(flat.offset + len(flat)) * 4])
    rows = table.drop_columns(["embedding"]).to_pylist()
    for i, r in enumerate(rows):
        r["embedding"] = values[i * dim:(i + 1) * dim]
    return rows


def photo_row(p: Photo, vec: Sequence[float], source: str, run_id: str, prep_version: str, at: str) -> dict:
    return {"desertion_no": p.desertion_no, "image_slot": p.slot, "photo_version": p.version, "sha256": p.sha256,
            "s3_key": p.s3_key, "embedding": list(vec), "source": source,
            "embed_run_id": run_id, "prep_version": prep_version, "embedded_at": at}


@dataclass
class Staged:
    """S3 에 이미 있는 결과. 다음 실행은 여기 없는 것만 보낸다."""

    rows: dict[tuple[str, int], dict] = field(default_factory=dict)  # 칸별 최신 행 (run_id 순)
    vectors: dict[str, list[float]] = field(default_factory=dict)  # sha256 → 벡터
    skipped: dict[str, str] = field(default_factory=dict)  # sha256 → 사유 (blank 등, 다시 보내지 않음)
    read_errors: dict[str, int] = field(default_factory=dict)  # Bronze s3_key → 읽기 실패한 실행 수 (다시 시도함)
    api_errors: dict[str, int] = field(default_factory=dict)  # sha256 → 모델 호출이 실패한 실행 수 (다시 보냄)
    runs: list[str] = field(default_factory=list)


def load_staged(s3, bucket: str, model: str, dim: int, prep_version: str,
                run_ids: Iterable[str] | None = None) -> Staged:
    """전처리 버전이 같은 행만 인정한다. run_ids 를 주면 그 실행만 읽는다."""
    from rebom.embedding.manifest import list_keys

    prefix = run_prefix(model, dim)
    keys = sorted(o["Key"] for o in list_keys(s3, bucket, prefix))
    wanted = set(run_ids) if run_ids is not None else None
    st = Staged()
    for key in keys:  # run_id 는 시각으로 시작해 이름순이 곧 실행순
        run = key[len(prefix):].split("/", 1)[0].removeprefix("run_id=")
        if wanted is not None and run not in wanted:
            continue
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        if key.endswith("/summary.json"):
            summary = json.loads(body)
            st.skipped.update(summary.get("skipped_sha256", {}))
            for sha in summary.get("api_error_sha256", {}):
                st.api_errors[sha] = st.api_errors.get(sha, 0) + 1
            for k in summary.get("read_error_keys", {}):
                st.read_errors[k] = st.read_errors.get(k, 0) + 1
            continue
        if not key.endswith(".parquet"):
            continue
        if run not in st.runs:
            st.runs.append(run)
        for r in read_parquet(body):
            if r["prep_version"] != prep_version:
                continue
            st.rows[(r["desertion_no"], r["image_slot"])] = r
            st.vectors[r["sha256"]] = r["embedding"]
    return st
