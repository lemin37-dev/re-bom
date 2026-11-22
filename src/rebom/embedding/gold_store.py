"""매일 사진 임베딩의 Gold 표 읽기·쓰기. embed·load·canary 에 --gold-env 를 주면 S3 parquet 대신 이 표가 원본이다.

규칙: docs/data_contract.md 13.4, 표 정의: rebom.common.gold_catalog (임베딩 묶음).

- 읽기 (read_state): gold_image_embedding 에서 이 모델·전처리 버전의 sha256 → 벡터,
  gold_embedding_inference_log 에서 다시 보내지 않을 사진(BLANK·FAILED)과 다시 읽을 사진(READ_ERROR).
  State.staged(current) 가 그 내용을 embed·load 가 쓰던 staging.Staged 모양으로 바꿔 준다(계획 코드는 그대로).
- 쓰기 (write_run): embed 실행 1번의 결과. 요청 기록 append → 결과 표·처리 기록은 이 사진들만 교체(overwrite).
  같은 실행을 다시 써도 결과가 같다.
- canary (write_canary): canary 표 1행 + 요청 기록(사진 6장, 요청 1번).

공고–사진 칸 연결은 Gold 에 두지 않는다(13.0). 칸은 지금처럼 Bronze manifest 에서 고른다.
parquet 은 되돌릴 때를 위해 embed 가 지금처럼 같이 쓴다(이 경로에서는 읽지 않음).
"""
from __future__ import annotations

import json
from array import array
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

from rebom.embedding import staging

IMAGE_TASK = "image_embedding"
SUBJECT_PHOTO = "photo"
OK, BLANK, FAILED, READ_ERROR, SHA_MISMATCH = "OK", "BLANK", "FAILED", "READ_ERROR", "SHA_MISMATCH"
SKIP_STATUSES = (BLANK, FAILED)  # 다시 보내지 않는다 (빈 사진, 열리지 않는 파일)


def model_id(model: str, dim: int) -> str:
    """Gold `model` 칸 값. 차원이 다르면 다른 벡터 공간이라 같이 둔다 (text_card.MODEL_KEY 와 같은 형식)."""
    return f"{model}:{dim}"


def log_version(model_value: str, prompt_version: str) -> str:
    """처리 기록의 `version`. 태깅 처리 기록과 같은 키(모델 칸 없음)라 모델을 함께 넣는다."""
    return f"{model_value}/{prompt_version}"


# ---- arrow 변환 ------------------------------------------------------------------------------------------------

def table_arrow(table, rows: list[dict[str, Any]], vector: bool = False):
    """표의 현재 스키마로 arrow 표를 만든다. 벡터는 파이썬 float 목록을 거치지 않고 float32 버퍼로 바로 만든다
    (3.6만 × 768 을 파이썬 float 로 만들면 1GB 가까이 쓴다)."""
    import pyarrow as pa

    schema = table.schema().as_arrow()
    cols = []
    for f in schema:
        if vector and f.name == "embedding":
            values = array("f")
            offsets = [0]
            for r in rows:
                values.extend(r["embedding"])
                offsets.append(len(values))
            flat = pa.Array.from_buffers(pa.float32(), len(values), [None, pa.py_buffer(values.tobytes())])
            if pa.types.is_large_list(f.type):  # pyiceberg as_arrow 는 large_list 를 준다
                cols.append(pa.LargeListArray.from_arrays(pa.array(offsets, pa.int64()), flat, type=f.type))
            else:
                cols.append(pa.ListArray.from_arrays(pa.array(offsets, pa.int32()), flat, type=f.type))
        else:
            cols.append(pa.array([r.get(f.name) for r in rows], type=f.type))
    return pa.Table.from_arrays(cols, schema=schema)


def vectors_of(arrow_table) -> list[array]:
    """embedding 칸 → 행마다 float32 array. 값은 표의 float32 그대로."""
    emb = arrow_table.column("embedding").combine_chunks()
    if len(emb) == 0:
        return []
    flat = emb.flatten()
    values = array("f")
    if len(flat):
        values.frombytes(flat.buffers()[1].to_pybytes()[flat.offset * 4:(flat.offset + len(flat)) * 4])
    offsets = emb.offsets.to_pylist()
    base = offsets[0]
    return [values[offsets[i] - base:offsets[i + 1] - base] for i in range(len(emb))]


# ---- 읽기 ------------------------------------------------------------------------------------------------------

@dataclass
class State:
    """Gold 에 이미 있는 것. 다음 실행은 여기 없는 sha256 만 보낸다."""

    vectors: dict[str, array] = field(default_factory=dict)  # sha256 → 벡터
    logs: dict[str, dict[str, Any]] = field(default_factory=dict)  # sha256 → 처리 기록 행

    def staged(self, current: dict) -> staging.Staged:
        """embed·load 계획 코드가 쓰는 모양. 현재 칸 중 sha256 벡터가 있는 칸은 '이미 있음' 행이 된다
        (Gold 는 사진 단위라 칸마다 복사(reuse)할 일이 없다)."""
        st = staging.Staged(vectors=dict(self.vectors))
        for key, p in current.items():
            vec = self.vectors.get(p.sha256)
            if vec is not None:
                st.rows[key] = {"sha256": p.sha256, "embedding": vec, "source": "gold"}
        for sha, r in self.logs.items():
            if sha in self.vectors:
                continue
            if r["status"] in SKIP_STATUSES:
                st.skipped[sha] = r["status"].lower()
            elif r["status"] == READ_ERROR:
                key = json.loads(r.get("detail") or "{}").get("s3_key")
                if key:
                    st.read_errors[key] = r.get("attempts") or 1
        return st


def read_state(catalog, env, model: str, dim: int, prep_version: str) -> State:
    from pyiceberg.expressions import And, EqualTo

    from rebom.common import gold_catalog as gc

    mid = model_id(model, dim)
    image = catalog.load_table(env.table(gc.IMAGE_EMBEDDING_TABLE)).scan(
        row_filter=And(EqualTo("model", mid), EqualTo("prompt_version", prep_version)),
        selected_fields=("subject_key", "embedding"),
    ).to_arrow()
    state = State(vectors=dict(zip(image.column("subject_key").to_pylist(), vectors_of(image))))
    logs = catalog.load_table(env.table(gc.EMBEDDING_INFERENCE_LOG_TABLE)).scan(
        row_filter=And(EqualTo("task", IMAGE_TASK), EqualTo("version", log_version(mid, prep_version))),
        selected_fields=("subject_key", "status", "attempts", "detail"),
    ).to_arrow().to_pylist()
    state.logs = {r["subject_key"]: r for r in logs}
    return state


# ---- 행 만들기 ----------------------------------------------------------------------------------------------------

def image_row(sha: str, vec: Sequence[float], mid: str, prep_version: str, run_id: str, at: datetime) -> dict:
    return {"subject_type": SUBJECT_PHOTO, "subject_key": sha, "task": IMAGE_TASK, "model": mid,
            "prompt_version": prep_version, "source_hash": sha, "embedding": vec, "gold_run_id": run_id,
            "gold_updated_at": at}


def log_row(sha: str, version: str, status: str, run_id: str, at: datetime, *, attempts: int = 1,
            detail: dict | None = None, last_error: str | None = None) -> dict:
    return {"subject_type": SUBJECT_PHOTO, "subject_key": sha, "task": IMAGE_TASK, "version": version,
            "input_hash": sha, "status": status, "attempts": attempts, "tag_count": None, "last_error": last_error,
            "gold_run_id": run_id, "gold_updated_at": at,
            "detail": json.dumps(detail, ensure_ascii=False) if detail is not None else None}


def _share(total, n: int, i: int):
    """요청 1번의 합계를 사진 n장에 나눈 i번째 몫. 나머지는 첫 행에 둬서 request_id 로 더하면 합계와 같다."""
    if total is None:
        return None
    if isinstance(total, int):
        base = total // n
        return total - base * (n - 1) if i == 0 else base
    share = total / n
    return total - share * (n - 1) if i == 0 else share


def request_rows(shas: Sequence[str], meta: dict[str, Any], prep_version: str, run_id: str,
                 status: str = OK, error_code: str | None = None) -> list[dict]:
    """요청 1번 → 사진마다 1행. 같은 request_id, batch_size = 사진 수, 비용·토큰은 몫, 지연·시도 수는 요청 값."""
    n = len(shas)
    at = meta["requested_at"]
    return [{
        "request_id": meta.get("request_id"), "subject_type": SUBJECT_PHOTO, "subject_key": sha, "task": IMAGE_TASK,
        "prompt_version": prep_version, "run_no": 1, "requested_model": meta.get("requested_model"),
        "response_model": meta.get("response_model"), "provider": meta.get("provider"), "canonical_slug": None,
        "media_resolution": None, "prompt_tokens": _share(meta.get("prompt_tokens"), n, i), "completion_tokens": None,
        "reasoning_tokens": None, "cost_usd": _share(meta.get("cost_usd"), n, i), "latency_ms": meta.get("latency_ms"),
        "attempts": meta.get("attempts"), "status": status, "error_code": error_code, "raw_answer": None,
        "requested_at": at, "gold_run_id": run_id, "dt": at.strftime("%Y-%m-%d"), "temperature": None,
        "reasoning_effort": None, "finish_reason": None, "native_finish_reason": None, "batch_size": n,
    } for i, sha in enumerate(shas)]


# ---- 쓰기 ------------------------------------------------------------------------------------------------------

def _scope(mid_or_version: tuple[str, str], shas: Iterable[str], log: bool):
    from pyiceberg.expressions import And, EqualTo, In

    a, b = mid_or_version
    first = And(EqualTo("task", IMAGE_TASK), EqualTo("version", b)) if log else \
        And(EqualTo("model", a), EqualTo("prompt_version", b))
    return And(first, In("subject_key", sorted(set(shas))))


def write_run(catalog, env, *, mid: str, prep_version: str, images: list[dict], logs: list[dict],
              requests: list[dict]) -> dict[str, int]:
    """요청 기록(비용) 먼저 append, 그다음 결과·처리 기록을 이 사진들 범위만 교체한다."""
    from rebom.common import gold_catalog as gc

    if requests:
        t = catalog.load_table(env.table(gc.EMBEDDING_REQUEST_LOG_TABLE))
        t.append(table_arrow(t, requests))
    if images:
        t = catalog.load_table(env.table(gc.IMAGE_EMBEDDING_TABLE))
        t.overwrite(table_arrow(t, images, vector=True),
                    overwrite_filter=_scope((mid, prep_version), (r["subject_key"] for r in images), log=False))
    if logs:
        t = catalog.load_table(env.table(gc.EMBEDDING_INFERENCE_LOG_TABLE))
        t.overwrite(table_arrow(t, logs),
                    overwrite_filter=_scope(("", log_version(mid, prep_version)), (r["subject_key"] for r in logs),
                                            log=True))
    return {"image": len(images), "log": len(logs), "request": len(requests)}


def write_canary(catalog, env, row: dict, requests: list[dict]) -> dict[str, int]:
    from pyiceberg.expressions import EqualTo

    from rebom.common import gold_catalog as gc

    if requests:
        t = catalog.load_table(env.table(gc.EMBEDDING_REQUEST_LOG_TABLE))
        t.append(table_arrow(t, requests))
    t = catalog.load_table(env.table(gc.EMBEDDING_CANARY_TABLE))
    t.overwrite(table_arrow(t, [row]), overwrite_filter=EqualTo("gold_run_id", row["gold_run_id"]))
    return {"canary": 1, "request": len(requests)}
