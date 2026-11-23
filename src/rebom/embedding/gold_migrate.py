"""옛 S3 parquet(칸당 1행) → Gold Iceberg 임베딩 표(사진당 1행)로 옮긴다. Gemini 는 부르지 않는다.

결정: docs/gold_rules_embedding_review_20261120.md (v3) 2·7절, 표 정의: rebom.common.gold_catalog.

- gold_image_embedding  sha256 마다 1행. 벡터는 Gemini 를 실제로 부른 행(source=api) 중 가장 늦은 실행의 값
                        (다음 실행의 재사용(reuse)·RDS 적재가 쓰던 값과 같다). gold_run_id 는 그 실행.
- gold_embedding_inference_log  sha256 마다 OK 1행. 빈 사진은 BLANK 1행 + detail {"gray_std", "blank_std"}
                        (Bronze 원본을 다시 읽어 점수를 잰다. 받지 못하면 사유만 남긴다)
- gold_embedding_canary checks/ 의 확인 파일마다 1행
- 요청 기록(gold_embedding_request_log)은 옛 실행에 요청별 기록이 없어 옮기지 않는다. 실행 합계는 각 summary.json 에 남아 있다.

다시 실행해도 같은 결과다: 이 모델·전처리 버전의 행을 통째로 바꿔 쓴다(overwrite). 다만 표에 옮기는 실행에 없는
gold_run_id 가 있으면(전환 뒤 DAG 가 쓴 행) 지우지 않도록 멈춘다.
<<<<<<< HEAD
=======
전환(embed --gold-env) 뒤에는 쓰지 않는다: 그 표에 직접 쓴 실행이 하나라도 있으면 멈춘다(direct_runs).
>>>>>>> e268e68adf2fab168d57dc21b029f29cd4929bb8
"""
from __future__ import annotations

import json
from array import array
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from rebom.embedding import canary, prep, staging
<<<<<<< HEAD
from rebom.embedding.manifest import Photo, list_keys

IMAGE_TASK = "image_embedding"
SUBJECT_PHOTO = "photo"
OK, BLANK = "OK", "BLANK"


def model_id(model: str, dim: int) -> str:
    """Gold `model` 칸 값. 차원이 다르면 다른 벡터 공간이라 같이 둔다 (text_card.MODEL_KEY 와 같은 형식)."""
    return f"{model}:{dim}"


def log_version(model_value: str, prompt_version: str) -> str:
    """처리 기록의 `version`. 태깅 처리 기록과 같은 키(모델 칸 없음)라 모델을 함께 넣는다."""
    return f"{model_value}/{prompt_version}"

=======
from rebom.embedding.gold_store import (  # noqa: F401 - 이 모듈 이름으로도 쓴다
    BLANK, IMAGE_TASK, OK, SUBJECT_PHOTO, log_version, model_id, table_arrow as _table_arrow, vectors_of,
)
from rebom.embedding.manifest import Photo, list_keys

>>>>>>> e268e68adf2fab168d57dc21b029f29cd4929bb8

def _ts(text: str | None) -> datetime | None:
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


@dataclass
class Run:
    run_id: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def read_runs(s3, bucket: str, model: str, dim: int) -> list[Run]:
    """실행 순(run_id 이름순)으로 parquet 행과 summary.json."""
    base = staging.run_prefix(model, dim)
    runs: dict[str, Run] = {}
    for key in sorted(o["Key"] for o in list_keys(s3, bucket, base)):
        run_id = key[len(base):].split("/", 1)[0].removeprefix("run_id=")
        run = runs.setdefault(run_id, Run(run_id))
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        if key.endswith("/summary.json"):
            run.summary = json.loads(body)
        elif key.endswith(".parquet"):
            run.rows.extend(staging.read_parquet(body))
    return [runs[k] for k in sorted(runs)]


<<<<<<< HEAD
=======
def direct_runs(runs: list[Run], env_name: str) -> list[str]:
    """embed --gold-env 로 이 환경 표에 직접 쓴 실행. 있으면 옮기기를 하지 않는다 (DAG 가 쓴 처리 기록을 덮어씀)."""
    return [r.run_id for r in runs if (r.summary.get("gold") or {}).get("env") == env_name]


>>>>>>> e268e68adf2fab168d57dc21b029f29cd4929bb8
@dataclass
class Plan:
    image: dict[str, dict[str, Any]]  # sha256 → 표 행 (embedding 은 array('f'))
    logs: list[dict[str, Any]]
    canary: list[dict[str, Any]]
    stats: dict[str, Any]


def build(runs: list[Run], model: str, dim: int, prep_version: str, canary_docs: Iterable[tuple[str, dict]] = (),
          blank_scores: dict[str, float] | None = None) -> Plan:
    mid = model_id(model, dim)
    image: dict[str, dict[str, Any]] = {}
    first_vec: dict[str, array] = {}
    rows_total = other_prep = vector_diff = 0
    sources: dict[str, int] = {}
    for run in runs:
        for r in run.rows:
            rows_total += 1
            if r["prep_version"] != prep_version:
                other_prep += 1
                continue
            sha, vec = r["sha256"], r["embedding"]
            sources[r["source"]] = sources.get(r["source"], 0) + 1
            if sha in first_vec and first_vec[sha] != vec:
                vector_diff += 1
            first_vec.setdefault(sha, vec)
            have = image.get(sha)
            # api 행이 있으면 api 행 중 마지막, 없으면(재사용만) 처음 본 행
            if have is None or (r["source"] == "api" and (have["_source"] != "api" or run.run_id >= have["gold_run_id"])):
                image[sha] = {
                    "subject_type": SUBJECT_PHOTO, "subject_key": sha, "task": IMAGE_TASK, "model": mid,
                    "prompt_version": prep_version, "source_hash": sha, "embedding": vec,
                    "gold_run_id": r["embed_run_id"] or run.run_id, "gold_updated_at": _ts(r["embedded_at"]),
                    "_source": r["source"],
                }
    version = log_version(mid, prep_version)
    logs = [{
        "subject_type": SUBJECT_PHOTO, "subject_key": sha, "task": IMAGE_TASK, "version": version, "input_hash": sha,
        "status": OK, "attempts": 1, "tag_count": None, "last_error": None, "gold_run_id": row["gold_run_id"],
        "gold_updated_at": row["gold_updated_at"], "detail": None,
    } for sha, row in sorted(image.items())]
    blank_scores = blank_scores or {}
    blanks: dict[str, tuple[str, str, str]] = {}  # sha → (사유, 실행, 시각)
    for run in runs:
        if run.summary.get("prep_version", prep_version) != prep_version:
            continue
        for sha, reason in run.summary.get("skipped_sha256", {}).items():
            blanks.setdefault(sha, (reason, run.run_id, run.summary.get("started_at")))
    for sha, (reason, run_id, at) in sorted(blanks.items()):
        if sha in image:  # 빈 사진으로 건너뛰었다가 나중에 임베딩된 일은 없어야 한다
            continue
        score = blank_scores.get(sha)
        detail = ({"gray_std": round(score, 4), "blank_std": prep.BLANK_STD} if score is not None
                  else {"reason": reason, "gray_std": None, "blank_std": prep.BLANK_STD})
        logs.append({
            "subject_type": SUBJECT_PHOTO, "subject_key": sha, "task": IMAGE_TASK, "version": version,
            "input_hash": sha, "status": BLANK if reason == "blank" else reason.upper(), "attempts": 1,
            "tag_count": None, "last_error": None, "gold_run_id": run_id, "gold_updated_at": _ts(at),
            "detail": json.dumps(detail, ensure_ascii=False),
        })
    canary_rows = [canary_row(key, doc, mid) for key, doc in canary_docs]
    stats = {
        "runs": [r.run_id for r in runs], "parquet_rows": rows_total, "rows_by_source": sources,
        "other_prep_version_rows": other_prep, "image_rows": len(image),
        "image_rows_without_api_row": sum(1 for v in image.values() if v["_source"] != "api"),
        "copies_with_different_vector": vector_diff,
        "log_rows": len(logs), "log_ok": len(image), "log_blank": len(logs) - len(image),
        "blank_without_score": sum(1 for s in blanks if s not in image and s not in blank_scores),
        "canary_rows": len(canary_rows), "model": mid, "prompt_version": prep_version, "log_version": version,
    }
    return Plan(image, logs, canary_rows, stats)


def canary_row(key: str, doc: dict[str, Any], mid: str) -> dict[str, Any]:
    usage = doc.get("api_usage") or {}
    checked = _ts(doc.get("checked_at"))
    run_id = usage.get("session_id") or f"canary-{checked:%Y%m%dT%H%M%S}"
    return {
        "gold_run_id": run_id, "task": IMAGE_TASK, "model": mid, "prompt_version": doc.get("prep_version", prep.PREP_VERSION),
        "checked_at": checked, "passed": bool(doc.get("ok")), "threshold": doc.get("threshold"),
        "min_cosine": doc.get("min_cosine"), "photo_count": len(doc.get("cosines") or {}),
        "created_reference": doc.get("created_reference"), "reference_created_at": doc.get("reference_created_at"),
        "detail": json.dumps({"check_key": key, "cosines": doc.get("cosines"), "api_usage": usage}, ensure_ascii=False),
        "gold_updated_at": checked,
    }


def read_canary_docs(s3, bucket: str, model: str, dim: int) -> list[tuple[str, dict]]:
    base = canary.prefix(model, dim) + "checks/"
    return [(k, json.loads(s3.get_object(Bucket=bucket, Key=k)["Body"].read()))
            for k in sorted(o["Key"] for o in list_keys(s3, bucket, base)) if k.endswith(".json")]


def measure_blanks(s3, bucket: str, runs: list[Run], current: dict[tuple[str, int], Photo]) -> dict[str, float]:
    """빈 사진으로 건너뛴 sha256 의 판정 점수(회색조 표준편차). 지금 그 sha256 을 쓰는 칸의 Bronze 원본을 읽는다."""
    want = {sha for r in runs for sha in r.summary.get("skipped_sha256", {})}
    keys = {}
    for p in current.values():
        if p.sha256 in want:
            keys.setdefault(p.sha256, p.s3_key)
    scores = {}
    for sha, key in keys.items():
        try:
            scores[sha] = prep.blank_std(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        except Exception:  # noqa: BLE001 - 읽지 못하면 점수 없이 사유만 남긴다
            continue
    return scores


# ---- Iceberg 쓰기·검증 -------------------------------------------------------------------------------------------

<<<<<<< HEAD
def _table_arrow(table, rows: list[dict[str, Any]], vector: bool = False):
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


=======
>>>>>>> e268e68adf2fab168d57dc21b029f29cd4929bb8
def _foreign_runs(table, row_filter, allowed: set[str]) -> set[str]:
    have = {r["gold_run_id"] for r in table.scan(row_filter=row_filter, selected_fields=("gold_run_id",))
            .to_arrow().to_pylist()}
    return have - allowed


class ForeignRowsError(RuntimeError):
    """표에 옮기는 실행이 아닌 gold_run_id 행이 있다 (전환 뒤 DAG 가 쓴 행일 수 있어 지우지 않는다)."""


def write(catalog, env, plan: Plan) -> dict[str, int]:
    from pyiceberg.expressions import And, EqualTo

    from rebom.common import gold_catalog as gc

    mid, pv = plan.stats["model"], plan.stats["prompt_version"]
    image_t = catalog.load_table(env.table(gc.IMAGE_EMBEDDING_TABLE))
    log_t = catalog.load_table(env.table(gc.EMBEDDING_INFERENCE_LOG_TABLE))
    canary_t = catalog.load_table(env.table(gc.EMBEDDING_CANARY_TABLE))
    image_f = And(EqualTo("model", mid), EqualTo("prompt_version", pv))
    log_f = And(EqualTo("task", IMAGE_TASK), EqualTo("version", plan.stats["log_version"]))
    canary_f = And(EqualTo("task", IMAGE_TASK), EqualTo("model", mid))
    runs = {r["gold_run_id"] for r in plan.logs} | set(plan.stats["runs"])
    checks = [(image_t, image_f, runs), (log_t, log_f, runs), (canary_t, canary_f, {r["gold_run_id"] for r in plan.canary})]
    for table, flt, allowed in checks:
        foreign = _foreign_runs(table, flt, allowed)
        if foreign:
            raise ForeignRowsError(f"{table.name()}: 옮기는 실행이 아닌 gold_run_id {sorted(foreign)[:5]} — 멈춤")
    image_t.overwrite(_table_arrow(image_t, [plan.image[s] for s in sorted(plan.image)], vector=True),
                      overwrite_filter=image_f)
    log_t.overwrite(_table_arrow(log_t, plan.logs), overwrite_filter=log_f)
    canary_t.overwrite(_table_arrow(canary_t, plan.canary), overwrite_filter=canary_f)
    return {"image": len(plan.image), "log": len(plan.logs), "canary": len(plan.canary)}


def verify(catalog, env, plan: Plan) -> dict[str, Any]:
    """표에서 다시 읽어 행 수·키·벡터가 parquet 에서 만든 값과 같은지 본다."""
    from pyiceberg.expressions import And, EqualTo

    from rebom.common import gold_catalog as gc

    mid, pv = plan.stats["model"], plan.stats["prompt_version"]
    t = catalog.load_table(env.table(gc.IMAGE_EMBEDDING_TABLE))
    got = t.scan(row_filter=And(EqualTo("model", mid), EqualTo("prompt_version", pv)),
                 selected_fields=("subject_key", "embedding", "gold_run_id")).to_arrow()
    keys = got.column("subject_key").to_pylist()
    runs = got.column("gold_run_id").to_pylist()
<<<<<<< HEAD
    emb = got.column("embedding").combine_chunks()
    flat = emb.flatten()
    values = array("f")
    values.frombytes(flat.buffers()[1].to_pybytes()[flat.offset * 4:(flat.offset + len(flat)) * 4])
    offsets = emb.offsets.to_pylist()
    vec_diff = run_diff = dim_bad = 0
    for i, sha in enumerate(keys):
        want = plan.image.get(sha)
        v = values[offsets[i]:offsets[i + 1]]
=======
    vecs = vectors_of(got)
    vec_diff = run_diff = dim_bad = 0
    for i, sha in enumerate(keys):
        want = plan.image.get(sha)
        v = vecs[i]
>>>>>>> e268e68adf2fab168d57dc21b029f29cd4929bb8
        if want is None:
            continue
        if len(v) != len(want["embedding"]):
            dim_bad += 1
        elif v != want["embedding"]:
            vec_diff += 1
        if runs[i] != want["gold_run_id"]:
            run_diff += 1
    log_t = catalog.load_table(env.table(gc.EMBEDDING_INFERENCE_LOG_TABLE))
    logs = log_t.scan(row_filter=And(EqualTo("task", IMAGE_TASK), EqualTo("version", plan.stats["log_version"])),
                      selected_fields=("status",)).to_arrow().column("status").to_pylist()
    canary_n = catalog.load_table(env.table(gc.EMBEDDING_CANARY_TABLE)).scan(
        row_filter=And(EqualTo("task", IMAGE_TASK), EqualTo("model", mid)), selected_fields=("gold_run_id",)
    ).to_arrow().num_rows
    result = {
        "image_rows": len(keys), "image_expected": len(plan.image), "unique_keys": len(set(keys)),
        "missing": len(set(plan.image) - set(keys)), "extra": len(set(keys) - set(plan.image)),
        "vector_diff": vec_diff, "dim_mismatch": dim_bad, "run_id_diff": run_diff,
        "log_rows": len(logs), "log_expected": len(plan.logs),
        "log_by_status": {s: logs.count(s) for s in sorted(set(logs))},
        "canary_rows": canary_n, "canary_expected": len(plan.canary),
    }
    result["ok"] = (result["image_rows"] == result["image_expected"] == result["unique_keys"]
                    and not (result["missing"] or result["extra"] or vec_diff or dim_bad or run_diff)
                    and result["log_rows"] == result["log_expected"] and canary_n == len(plan.canary))
    return result
