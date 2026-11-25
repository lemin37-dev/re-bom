"""동기화 입력 읽기: Silver 완료 기록(S3 JSON), Silver·Gold Iceberg 표. 읽기만 한다.

- 공고 완료 기록: <notice-completion-prefix>/<bronze_run_id>.json (status COMPLETED, notice_snapshot_id, source_fetched_at).
  Silver 공고 적재·검증이 끝나면 run_silver_once 가 쓴다. 사진 처리를 기다리지 않는다.
- 발행 기록(READY): <publish-prefix>/<publish_id>.json, 순번은 <publish-prefix>/_sequence/slots/<20자리>.json.
  공고·사진 스냅샷 ID 가 함께 있어 같은 시점의 공고·사진을 읽는다(rebom_publish_silver_gold.py).
- Gold 행은 활성 버전(task@N)으로 고른다. version 칸이 비어 있는 옛 행은 그 버전의 레지스트리 설정과 같은
  옛 키(모델·질문지/전처리/템플릿)로 읽는다(data_contract 13.6 "기존 결과는 기존 키로 계속 읽는다").
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Iterable, Sequence

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_SLOT = re.compile(r"([0-9]{20})\.json$")
TEXT_TAG_DONE = ("OK", "NO_RESULT")


def _read_json(s3, bucket: str, key: str) -> dict[str, Any] | None:
    from botocore.exceptions import ClientError

    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise


def _keys(s3, bucket: str, prefix: str) -> Iterable[str]:
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            yield obj["Key"]


def notice_completion(s3, bucket: str, prefix: str, run_id: str | None = None) -> dict[str, Any]:
    """run_id 를 주면 그 기록, 없으면 run_id 가 가장 늦은(시각 순) COMPLETED 기록."""
    base = prefix.rstrip("/") + "/"
    if run_id is None:
        names = sorted(k[len(base):-5] for k in _keys(s3, bucket, base)
                       if k.endswith(".json") and "/" not in k[len(base):])
        names = [n for n in names if _ID.fullmatch(n)]
        if not names:
            raise RuntimeError(f"공고 완료 기록이 없습니다: s3://{bucket}/{base}")
        run_id = names[-1]
    if not _ID.fullmatch(run_id):
        raise ValueError(f"run_id 형식 오류: {run_id!r}")
    rec = _read_json(s3, bucket, f"{base}{run_id}.json")
    if rec is None:
        raise RuntimeError(f"공고 완료 기록이 없습니다: {run_id}")
    if rec.get("status") != "COMPLETED" or rec.get("bronze_run_id") != run_id:
        raise RuntimeError(f"COMPLETED 기록이 아닙니다: {run_id}")
    if type(rec.get("notice_snapshot_id")) is not int or not rec.get("notice_table") or not rec.get("source_fetched_at"):
        raise RuntimeError(f"공고 완료 기록에 스냅샷·표·수집 시각이 없습니다: {run_id}")
    return rec


def ready_publication(s3, bucket: str, prefix: str, publish_id: str | None = None) -> dict[str, Any]:
    """publish_id 를 주면 그 발행, 없으면 READY 기록이 있는 가장 큰 순번의 발행. 순번은 기록의 publish_seq 와 맞아야 한다."""
    base = prefix.rstrip("/")
    if publish_id is not None:
        if not _ID.fullmatch(publish_id):
            raise ValueError(f"publish_id 형식 오류: {publish_id!r}")
        rec = _read_json(s3, bucket, f"{base}/{publish_id}.json")
        if rec is None:
            raise RuntimeError(f"발행 기록이 없습니다: {publish_id}")
        return _check_ready(rec, publish_id)
    seqs = sorted((int(m.group(1)), k) for k in _keys(s3, bucket, f"{base}/_sequence/slots/")
                  if (m := _SLOT.search(k)))
    for seq, key in reversed(seqs):  # 순번을 잡고 기록을 아직 못 쓴 발행은 건너뛴다
        slot = _read_json(s3, bucket, key) or {}
        pid = slot.get("publish_id")
        if slot.get("publish_seq") != seq or not isinstance(pid, str) or not _ID.fullmatch(pid):
            raise RuntimeError(f"발행 순번 기록 오류: {key}")
        rec = _read_json(s3, bucket, f"{base}/{pid}.json")
        if rec is not None and rec.get("status") == "READY":
            return _check_ready(rec, pid)
    raise RuntimeError(f"READY 발행 기록이 없습니다: s3://{bucket}/{base}/")


def _check_ready(rec: dict[str, Any], publish_id: str) -> dict[str, Any]:
    if rec.get("publish_id") != publish_id or rec.get("status") != "READY":
        raise RuntimeError(f"READY 발행이 아닙니다: {publish_id}")
    for f in ("publish_seq", "notice_snapshot_id", "photo_snapshot_id"):
        if type(rec.get(f)) is not int or rec[f] <= 0:
            raise RuntimeError(f"발행 기록의 {f} 오류: {publish_id}")
    for f in ("notice_table", "photo_table", "photo_run_id"):
        if not isinstance(rec.get(f), str) or not rec[f].strip():
            raise RuntimeError(f"발행 기록의 {f} 오류: {publish_id}")
    return rec


# ---- Iceberg ----------------------------------------------------------------------------------------------------

def scan(catalog, table_name: str, *, snapshot_id: int | None = None, fields: Sequence[str] | None = None,
         row_filter=None) -> list[dict[str, Any]]:
    table = catalog.load_table(table_name)
    if snapshot_id is not None and table.snapshot_by_id(snapshot_id) is None:
        raise RuntimeError(f"스냅샷이 없습니다: {table_name}@{snapshot_id}")
    kwargs: dict[str, Any] = {}
    if snapshot_id is not None:
        kwargs["snapshot_id"] = snapshot_id
    if fields is not None:
        kwargs["selected_fields"] = tuple(fields)
    if row_filter is not None:
        kwargs["row_filter"] = row_filter
    return table.scan(**kwargs).to_arrow().to_pylist()


def shelters(catalog, table_name: str) -> dict[str, dict[str, Any]]:
    rows = scan(catalog, table_name, fields=("care_reg_no", "care_name", "care_tel", "care_address"))
    return {str(r["care_reg_no"]).strip(): r for r in rows if r.get("care_reg_no")}


# ---- Gold 버전 ---------------------------------------------------------------------------------------------------

def registry_config(catalog, env, task: str, version: str) -> tuple[dict[str, Any], str]:
    """(설정, config_hash). 레지스트리의 해시까지 확인한다(gold_version_registry.resolve_version)."""
    from rebom.common.gold_version_registry import canonical_config, resolve_version

    config = resolve_version(catalog, env, task, version)
    return config, canonical_config(config)[1]


def legacy_key(task: str, config: dict[str, Any]) -> tuple[str | None, str | None]:
    """version 칸이 빈 옛 Gold 행을 읽을 (model, prompt_version). 모를 때는 (None, None) = 옛 행을 읽지 않음."""
    model = config.get("model_id")
    dim = config.get("dim")
    if task == "image_embedding":
        return (f"{model}:{dim}" if model and dim else None), config.get("preprocess_version")
    if task == "text_embedding":
        return (f"{model}:{dim}" if model and dim else None), config.get("template_version")
    return model, config.get("prompt_version")


def version_filter(task: str, version: str, config: dict[str, Any], *, legacy: bool, has_model: bool = True):
    from pyiceberg.expressions import And, EqualTo, IsNull, Or

    cond = EqualTo("version", version)
    model, prompt = legacy_key(task, config)
    if legacy and prompt:
        old = And(IsNull("version"), EqualTo("prompt_version", prompt))
        if has_model and model:
            old = And(old, EqualTo("model", model))
        cond = Or(cond, old)
    return And(EqualTo("task", task), cond)


def gold_rows(catalog, env, base_table: str, task: str, version: str, config: dict[str, Any],
              fields: Sequence[str], *, legacy: bool = True, has_model: bool = True) -> list[dict[str, Any]]:
    return scan(catalog, env.table(base_table), fields=fields,
                row_filter=version_filter(task, version, config, legacy=legacy, has_model=has_model))


def image_vectors(catalog, env, version: str, config: dict[str, Any], shas: set[str], *,
                  legacy: bool = True) -> dict[str, dict[str, Any]]:
    """sha256 → {embedding(float 목록), gold_run_id}. 필요한 sha256 만 남긴다."""
    from rebom.common import gold_catalog as gc
    from rebom.embedding.gold_store import vectors_of

    table = catalog.load_table(env.table(gc.IMAGE_EMBEDDING_TABLE))
    arrow = table.scan(row_filter=version_filter("image_embedding", version, config, legacy=legacy),
                       selected_fields=("subject_key", "gold_run_id", "embedding")).to_arrow()
    out: dict[str, dict[str, Any]] = {}
    for sha, run, vec in zip(arrow.column("subject_key").to_pylist(), arrow.column("gold_run_id").to_pylist(),
                             vectors_of(arrow)):
        if sha in shas:
            if sha in out:
                raise RuntimeError(f"같은 버전에 같은 사진 벡터가 두 개 있음: {sha}")
            out[sha] = {"embedding": list(vec), "gold_run_id": run}
    return out


def text_vectors(catalog, env, version: str, config: dict[str, Any], *, legacy: bool = True) -> list[dict[str, Any]]:
    from rebom.common import gold_catalog as gc
    from rebom.embedding.gold_store import vectors_of

    table = catalog.load_table(env.table(gc.TEXT_EMBEDDING_TABLE))
    arrow = table.scan(row_filter=version_filter("text_embedding", version, config, legacy=legacy),
                       selected_fields=("subject_key", "source_hash", "gold_run_id", "gold_updated_at",
                                        "embedding")).to_arrow()
    cols = {c: arrow.column(c).to_pylist() for c in ("subject_key", "source_hash", "gold_run_id", "gold_updated_at")}
    return [{**{c: cols[c][i] for c in cols}, "embedding": list(v)} for i, v in enumerate(vectors_of(arrow))]


def text_tag_inputs(catalog, env, task: str, version: str, config: dict[str, Any], *,
                    legacy: bool = True) -> dict[tuple[str, str], str]:
    """(공고, 작업) → 최신 입력 해시. 그 공고·작업·버전의 처리 기록 중 가장 늦은 행이 OK·NO_RESULT 일 때만
    (NO_TEXT·FAILED 면 활성 태그 없음, data_contract 15.5)."""
    from pyiceberg.expressions import And, EqualTo, In

    from rebom.common import gold_catalog as gc

    keys = [version] + ([config["prompt_version"]] if legacy and config.get("prompt_version") else [])
    rows = scan(catalog, env.table(gc.INFERENCE_LOG_TABLE),
                fields=("subject_key", "input_hash", "status", "gold_updated_at"),
                row_filter=And(And(EqualTo("task", task), EqualTo("subject_type", "animal")), In("version", keys)))
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        cur = latest.get(r["subject_key"])
        if cur is None or (r["gold_updated_at"], r["input_hash"]) > (cur["gold_updated_at"], cur["input_hash"]):
            latest[r["subject_key"]] = r
    return {(no, task): r["input_hash"] for no, r in latest.items() if r["status"] in TEXT_TAG_DONE}


def group_tasks(active: dict[str, dict[str, Any]], tasks: Iterable[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for t in tasks:
        out["active" if t in active else "inactive"].append(t)
    return dict(out)
