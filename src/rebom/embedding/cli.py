"""사진 임베딩 실행 진입점. 순서: canary → embed → load(RDS). 사람이 확인할 때 staged·smoke.

결과 원본: --gold-env prod 를 주면 Gold Iceberg 임베딩 묶음 표(gold_store.py)를 읽고 쓴다. 매일 DAG 는 prod 를 준다.
주지 않으면 전환 전처럼 S3 parquet 만 쓴다. 전환 뒤에도 embed 는 parquet 을 같이 써 둬서(읽지는 않음)
PR 을 되돌리면 parquet 경로가 빈틈 없이 이어진다. staged·smoke·canary --reset 은 parquet 을 읽는다.

    python -m rebom.embedding.cli discover [--ping] [--db] [--sha-sample N]
        읽기만 한다. Bronze 사진 수·확장자·용량, manifest 위치·칼럼, manifest↔S3 대조, Silver 테이블,
        Gemini 키(길이만), --ping 은 모델 정보 조회(과금 없음), --db 는 pgvector·테이블 상태.
    python -m rebom.embedding.cli embed --limit N [--dry-run] [--workers 4] [--image-batch 1] [--gold-env prod]
        manifest 의 칸별 최신 사진 중 S3 결과에 없거나 sha256 이 바뀐 것만, 같은 sha256 은 1번만 보낸다.
        sha256 이 manifest 와 다르거나 빈 사진이면 보내지 않는다. 전처리는 prep.py, 결과는 S3 parquet(staging.py).
        Bronze 파일을 읽지 못하면 그 사진만 건너뛰고 summary 의 read_error_keys 에 남겨 다음 실행에서 다시 읽는다.
        RDS 에는 쓰지 않는다. --gold-env 면 Gold 결과·처리 기록·요청 기록 표에도 쓴다(요청 1번 = 1행).
        요청 1번에 사진 --image-batch 장(기본 1, 매일 실행). 전체 재임베딩처럼 시간이 중요할 때만 6 까지 묶는다.
        빈 사진은 처리 기록 detail 에 판정 점수, OK 행 detail 에는 그 벡터를 만든 request_id 를 남긴다.
    python -m rebom.embedding.cli staged [--run-id ID]
        S3 결과 점검 (과금 없음).
    python -m rebom.embedding.cli load [--run-id ID] [--dry-run] [--full] [--gold-env prod]
        S3 결과(--gold-env 면 Gold 표) → RDS image_embedding (확인·승인 뒤). manifest 의 현재 sha256 과 RDS sha256 을
        비교해 변경분만 쓴다.
    python -m rebom.embedding.cli canary [--gold-env prod]
        고정 사진 6장 재임베딩 → 기준과 코사인. --gold-env 면 결과를 Gold canary 표·요청 기록 표에도 남긴다.
    python -m rebom.embedding.cli smoke [--sample 10] [--trunc 3]
        RDS 벡터 차원·manifest 대조, 사진 질의로 자기 자신 찾기, 재임베딩 일치, 3072 앞부분 비교, 지연 p95.
    python -m rebom.embedding.cli compare-sources [--silver-env test]
        Bronze manifest 와 Silver 이미지 표의 현재 사진 대조 (읽기만). switch_ready 가 true 여야 Silver 로 넘어간다.
    python -m rebom.embedding.cli gold-migrate --gold-env test|prod [--write] [--profile rebom]
        옛 S3 parquet → Gold Iceberg 임베딩 표 (사진당 1행, Gemini 호출 없음, gold_migrate.py).
        기본은 계획만 출력. --write 면 쓰고 다시 읽어 검증한다. 표는 gold_catalog --create 로 먼저 만든다(사람).
        그 환경 표에 embed --gold-env 로 직접 쓴 실행이 있으면(전환 뒤) 멈춘다.

현재 사진을 읽는 곳 (embed·staged·load·smoke): --source bronze(기본, 사진 manifest) | silver(Silver 이미지 표).
silver 는 Silver 가 운영(apply)으로 열린 뒤 쓴다. --silver-env test 로는 S3·RDS 에 쓰지 않는다 (sources.py).

환경 변수: REBOM_S3_BUCKET(필수), AWS_PROFILE·AWS_DEFAULT_REGION, REBOM_GEMINI_SECRET_ID, REBOM_DB_SECRET_ID,
REBOM_DB_HOST·REBOM_DB_PORT(포트포워딩 때). 비밀값은 출력하지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
from collections import Counter
from typing import Any

from rebom.embedding import manifest as mf

GLUE_DATABASE = "rebom_silver"


def _bucket() -> str:
    bucket = os.environ.get("REBOM_S3_BUCKET")
    if not bucket:
        sys.exit("REBOM_S3_BUCKET 이 없습니다 (예: rebom-data-559987919299)")
    return bucket


def _s3():
    import boto3

    return boto3.client("s3", region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2"))


def _current(args: argparse.Namespace, s3: Any, bucket: str, writes: bool = False):
    """--source 에 따라 현재 사진을 읽는다. (현재 사진, manifest 목록 또는 None)

    silver test 표는 현재 사진의 일부만 담고 사진 교체(v2)도 반영 전이라, S3·RDS 에 쓰는 실행(writes)은 막는다.
    Silver 로 넘어가는 조건과 순서: docs/image_embedding_status_20261120.md 4절."""
    from rebom.embedding import sources

    source = getattr(args, "source", sources.DEFAULT_SOURCE)
    silver_env = getattr(args, "silver_env", "test")
    if source == "silver" and writes and silver_env == "test":
        sys.exit("--source silver --silver-env test 로는 S3·RDS 에 쓰지 않습니다 (Silver test 표는 현재 사진의 일부만 담음). "
                 "--dry-run 이나 compare-sources 로 확인만 해 주세요.")
    current, manifests = sources.load_current(source, s3, bucket, silver_env)
    if source == "silver":
        print(f"입력: Silver 이미지 표 ({silver_env}), 현재 사진 {len(current)}칸")
    return current, manifests


def _gold_target(args: argparse.Namespace):
    """--gold-env 가 있으면 (카탈로그, Gold 환경), 없으면 None (parquet 경로)."""
    name = getattr(args, "gold_env", None)
    if not name:
        return None
    from rebom.common import gold_catalog as gc
    from rebom.storage.silver.aws_catalog import get_aws_catalog

    return get_aws_catalog(profile=getattr(args, "profile", None)), gc.get_gold_environment(name)


def _gold_state(gold, dim: int, *, version: str | None = None):
    from rebom.embedding import gemini, gold_store, prep

    catalog, env = gold
    state = gold_store.read_state(catalog, env, gemini.MODEL, dim, prep.PREP_VERSION, version=version)
    print(f"원본: Gold {env.name} 표 ({env.location_prefix}), 사진 벡터 {len(state.vectors)}개, 처리 기록 {len(state.logs)}행")
    return state


def _print(title: str, value: Any) -> None:
    print(f"== {title} ==")
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def discover(args: argparse.Namespace) -> int:
    bucket, s3 = _bucket(), _s3()

    objs = list(mf.list_keys(s3, bucket, mf.IMAGES_PREFIX))
    by_ext: Counter = Counter()
    size_by_ext: Counter = Counter()
    for o in objs:
        ext = o["Key"].rsplit(".", 1)[-1].lower()
        by_ext[ext] += 1
        size_by_ext[ext] += o["Size"]
    _print("bronze images", {
        "prefix": f"s3://{bucket}/{mf.IMAGES_PREFIX}",
        "objects": len(objs),
        "notices": len({o["Key"].split("/")[3] for o in objs}),
        "by_ext": {e: {"count": c, "GB": round(size_by_ext[e] / 1e9, 2)} for e, c in by_ext.items()},
    })

    manifests = mf.load_manifests(s3, bucket)
    summary = mf.summarize(manifests)
    summary["location"] = f"s3://{bucket}/{mf.MANIFEST_PREFIX}dt=<날짜>/run_id=<id>/manifest.json"
    current = mf.latest_photos(manifests)
    s3_keys = {o["Key"] for o in objs}
    cur_keys = {p.s3_key for p in current.values()}
    summary["check"] = {
        "current_photos_missing_in_s3": len(cur_keys - s3_keys),
        "s3_objects_not_current": len(s3_keys - cur_keys),  # 교체된 옛 버전 등
    }
    _print("photo manifests", summary)

    if args.sha_sample:
        sample = random.Random(0).sample(sorted(current.values(), key=lambda p: p.key),
                                         min(args.sha_sample, len(current)))
        bad = [p.s3_key for p in sample
               if hashlib.sha256(s3.get_object(Bucket=bucket, Key=p.s3_key)["Body"].read()).hexdigest() != p.sha256]
        _print("sha256 sample", {"checked": len(sample), "mismatch": len(bad), "mismatch_keys": bad[:5]})

    try:
        import boto3

        glue = boto3.client("glue", region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2"))
        tables = [t["Name"] for page in glue.get_paginator("get_tables").paginate(DatabaseName=GLUE_DATABASE)
                  for t in page["TableList"]]
        _print(f"silver tables (glue {GLUE_DATABASE})", tables)
    except Exception as exc:  # noqa: BLE001 - discover 는 끝까지 보여 준다
        _print("silver tables", f"조회 실패: {type(exc).__name__}: {exc}")

    from rebom.embedding import gemini

    try:
        key = gemini.load_api_key()
        _print("gemini key", {"found": True, "length": len(key)})
    except Exception as exc:  # noqa: BLE001
        _print("gemini key", {"found": False, "error": str(exc)})
        key = None

    if args.ping and key and gemini.is_openrouter_key(key):
        import urllib.request

        req = urllib.request.Request("https://openrouter.ai/api/v1/embeddings/models",
                                     headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # 모델 목록 조회, 과금 없음
            models = {m["id"]: m for m in json.loads(resp.read()).get("data", [])}
        m = models.get(gemini.OPENROUTER_MODEL)
        _print("openrouter ping (embeddings/models, 과금 없음)", {
            "model": gemini.OPENROUTER_MODEL, "available": m is not None,
            "input_modalities": (m or {}).get("architecture", {}).get("input_modalities"),
            "pricing": (m or {}).get("pricing"),
        })
    elif args.ping and key:
        from google import genai

        client = genai.Client(api_key=key)  # 변수로 잡아 두지 않으면 요청 전에 닫힌다
        info = client.models.get(model=gemini.MODEL)
        _print("gemini ping (models.get, 과금 없음)", {
            "model": info.name, "input_token_limit": getattr(info, "input_token_limit", None),
            "actions": getattr(info, "supported_actions", None),
        })

    if args.db:
        from rebom.embedding import store

        with store.connect() as conn:
            conn.read_only = True
            ext = conn.execute("SELECT default_version, installed_version FROM pg_available_extensions "
                               "WHERE name = 'vector'").fetchone()
            exists = store.table_exists(conn)
            rows = conn.execute(f"SELECT model, count(*) FROM {store.TABLE} GROUP BY model").fetchall() if exists else []
            _print("rds", {"vector_available": ext[0] if ext else None, "vector_installed": ext[1] if ext else None,
                           "table_exists": exists, "rows_by_model": dict(rows)})
    return 0


# --- embed: 보내기 전 걸러내기 → 6장씩 동시 요청 → S3 parquet ----------------------------

def _chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i : i + n]


READ_ERROR_WARN_RUNS = 3  # 이만큼 연속으로 못 읽은 Bronze 파일은 사람이 확인하도록 경고
READ_ERROR_FAIL_MIN = 20  # 이번 실행에서 이 수 이상, 그리고 절반 넘게 못 읽으면 권한·네트워크 문제로 보고 실패 처리
EXIT_READ_ERRORS = 4


def _s3_error_code(exc: Exception) -> str:
    """boto3 ClientError 면 오류 코드(NoSuchKey, AccessDenied 등), 아니면 예외 이름."""
    resp = getattr(exc, "response", None)
    code = resp.get("Error", {}).get("Code") if isinstance(resp, dict) else None
    return str(code or type(exc).__name__)


def _new_run_id() -> str:
    import uuid

    return time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:6]


def embed(args: argparse.Namespace) -> int:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from datetime import datetime, timezone

    from rebom.embedding import gemini, gold_store, plan as planmod, prep, staging

    bucket, s3 = _bucket(), _s3()
    gold = _gold_target(args)
    candidate = getattr(args, "version", None)
    if candidate is not None:
        if gold is None:
            raise ValueError("--version requires --gold-env")
        from rebom.common import gold_catalog as gc
        from rebom.common.gold_version_registry import require_version_column, verify_runtime_config

        catalog, env = gold
        verify_runtime_config(catalog, env, gold_store.IMAGE_TASK, candidate, {
            "model_id": gemini.MODEL, "dim": args.dim,
            "preprocess_version": prep.PREP_VERSION, "blank_std": prep.BLANK_STD,
        })
        for table in (gc.IMAGE_EMBEDDING_TABLE, gc.REQUEST_LOG_TABLE):
            require_version_column(catalog.load_table(env.table(table)))
    current, manifests = _current(args, s3, bucket, writes=not args.dry_run)
    if gold is None:
        staged = staging.load_staged(s3, bucket, gemini.MODEL, args.dim, prep.PREP_VERSION)
    else:
        staged = _gold_state(gold, args.dim, version=candidate).staged(current)
    plan = planmod.build(manifests, staged, current)
    order = plan.send_order()[: args.limit]
    retry_keys = [k for p in plan.send.values() for k in [p[0].s3_key] if k in staged.read_errors]
    _print("plan (보내기 전)", {
        **plan.stats,
        "retry_read_errors": len(retry_keys),  # 지난 실행에서 Bronze 파일을 못 읽어 다시 시도하는 사진
        "this_run_sha256": len(order),
        "this_run_photos": sum(len(plan.send[s]) for s in order) + len(plan.reuse),
        "this_run_requests_max": -(-len(order) // args.image_batch),  # 빈 사진은 빠져 실제로는 더 적다
    })
    if args.dry_run:
        for sha in order[:5]:
            print("  would embed", plan.send[sha][0].s3_key, sha[:12], f"(칸 {len(plan.send[sha])}개)")
        return 0
    if not order and not plan.reuse:
        print("보낼 사진이 없습니다.")
        return 0

    run_id = _new_run_id()
    prefix = staging.run_prefix(gemini.MODEL, args.dim, run_id)
    if candidate is not None:
        prefix = f"gold/_work{gold[1].suffix}/embedding_candidates/{candidate}/{run_id}/"
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    embedder = gemini.GeminiEmbedder(dim=args.dim, session_id=f"embed-{run_id}")
    stats: Counter = Counter()
    skipped: dict[str, str] = {}
    sha_mismatch: list[str] = []
    read_errors: dict[str, str] = {}  # Bronze s3_key → 오류 코드. skipped 에 넣지 않아 다음 실행에서 다시 읽는다
    rows: list[dict] = []
    parts: list[str] = []
    mid = gold_store.model_id(gemini.MODEL, args.dim)
    version = candidate or gold_store.log_version(mid, prep.PREP_VERSION)
    gold_images: list[dict] = []  # Gold 결과 표 (사진당 1행)
    gold_logs: dict[str, dict] = {}  # sha256 → 처리 기록 행
    gold_requests: list[dict] = []  # 요청 기록 (요청 1번 = 1행)
    links: dict[str, dict] = {}  # sha256 → 그 벡터를 만든 요청 (처리 기록 detail)

    def row(p, vec, source: str) -> dict:
        return staging.photo_row(p, vec, source, run_id, prep.PREP_VERSION, at)

    def flush(final: bool = False) -> None:
        while rows and (final or len(rows) >= args.part_size):
            chunk, rows[:] = rows[: args.part_size], rows[args.part_size :]
            key = f"{prefix}part-{len(parts):05d}.parquet"
            s3.put_object(Bucket=bucket, Key=key, Body=staging.to_parquet(chunk, args.dim))
            parts.append(key)

    blank_scores: dict[str, float] = {}

    def prepare(sha: str):
        p = plan.send[sha][0]
        try:
            data = s3.get_object(Bucket=bucket, Key=p.s3_key)["Body"].read()
        except Exception as exc:  # noqa: BLE001 - 파일 없음·권한·네트워크. 그 사진만 건너뛴다
            return sha, None, f"read_error:{_s3_error_code(exc)}"
        if hashlib.sha256(data).hexdigest() != sha:
            return sha, None, "sha_mismatch"
        try:
            score = prep.blank_std(data)
            if score < prep.BLANK_STD:
                blank_scores[sha] = score
                return sha, None, "blank"
            return sha, prep.to_jpeg(data), None
        except Exception as exc:  # noqa: BLE001 - 열리지 않는 파일
            return sha, None, f"bad_image:{type(exc).__name__}"

    def work(batch: list[str]):
        ready, out = [], []
        for sha, jpeg, reason in map(prepare, batch):
            if jpeg is None:
                out.append((sha, None, reason))
            else:
                ready.append((sha, jpeg))
        request = None
        if ready:
            jpegs = [j for _, j in ready]
            if hasattr(embedder, "embed_images_recorded"):
                vecs, meta = embedder.embed_images_recorded(jpegs)
            else:  # 기록을 주지 않는 임베더 (테스트 등)
                vecs, meta = embedder.embed_images(jpegs), {"requested_at": datetime.now(timezone.utc)}
            out += [(sha, v, None) for (sha, _), v in zip(ready, vecs)]
            request = ([sha for sha, _ in ready], meta)
        return out, request

    def gold_log(sha: str, reason: str) -> dict:
        """보내지 않은 사진의 처리 기록. 빈 사진은 판정 점수, 읽기 실패는 다음 실행에서 셀 시도 수."""
        now = datetime.now(timezone.utc)
        key = plan.send[sha][0].s3_key
        kind, _, code = reason.partition(":")
        if kind == "blank":
            return gold_store.log_row(sha, version, gold_store.BLANK, run_id, now, detail={
                "gray_std": round(blank_scores[sha], 4), "blank_std": prep.BLANK_STD})
        if kind == "read_error":
            before = staged.read_errors.get(key, 0)
            return gold_store.log_row(sha, version, gold_store.READ_ERROR, run_id, now, attempts=before + 1,
                                      detail={"s3_key": key, "error": code}, last_error=reason)
        if kind == "sha_mismatch":
            return gold_store.log_row(sha, version, gold_store.SHA_MISMATCH, run_id, now, detail={"s3_key": key},
                                      last_error=reason)
        return gold_store.log_row(sha, version, gold_store.BAD_IMAGE, run_id, now, detail={"s3_key": key},
                                  last_error=reason)

    for p in plan.reuse:  # 이전 실행에서 만든 같은 sha256 벡터를 복사
        rows.append(row(p, staged.vectors[p.sha256], "reuse"))
        stats["reuse_rows"] += 1

    t0, done, status = time.time(), 0, "ok"
    next_report = 300
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(work, b) for b in _chunks(order, args.image_batch)]
            for f in as_completed(futures):
                results, request = f.result()
                if request is not None:
                    shas, meta = request
                    gold_requests.append(gold_store.request_row(shas, meta, prep.PREP_VERSION, run_id))
                    links.update({s: gold_store.request_link(meta, len(shas)) for s in shas})
                for sha, vec, reason in results:
                    done += 1
                    if vec is None:
                        gold_logs[sha] = gold_log(sha, reason)
                        stats[reason.split(":")[0]] += 1
                        if reason == "sha_mismatch":  # 일시 문제일 수 있어 다음 실행에서 다시 본다
                            sha_mismatch.append(plan.send[sha][0].s3_key)
                        elif reason.startswith("read_error"):  # 사진 받기 실패처럼 다음 실행에서 다시 읽는다
                            read_errors[plan.send[sha][0].s3_key] = reason.split(":", 1)[1]
                        else:  # 빈 사진·열리지 않는 파일은 다음 실행에서도 보내지 않는다
                            skipped[sha] = reason
                        continue
                    vec = prep.l2_normalize(vec)
                    if len(vec) != args.dim:
                        raise RuntimeError(f"벡터 차원 {len(vec)} != {args.dim}")
                    stats["api_sha256"] += 1
                    now = datetime.now(timezone.utc)
                    gold_images.append(gold_store.image_row(sha, vec, mid, prep.PREP_VERSION, run_id, now))
                    gold_logs[sha] = gold_store.log_row(sha, version, gold_store.OK, run_id, now, detail=links.get(sha))
                    for i, p in enumerate(plan.send[sha]):  # 같은 파일을 쓰는 칸은 벡터를 나눠 쓴다
                        rows.append(row(p, vec, "api" if i == 0 else "dup"))
                        stats["api_rows" if i == 0 else "dup_rows"] += 1
                flush()
                if done >= next_report:
                    el = time.time() - t0
                    print(f"  {done}/{len(order)} sha256 ({el:.0f}s, {el / done:.3f}s/장)")
                    next_report += 300
    except BaseException:
        status = "aborted"
        raise
    finally:
        flush(final=True)
        gold_info, gold_error = None, None
        if gold is not None:  # 실행이 끊겨도 이미 돈을 낸 벡터는 남긴다
            gold_info = {"env": gold[1].name}
            try:
                gold_info["written"] = gold_store.write_run(
                    gold[0], gold[1], mid=mid, prep_version=prep.PREP_VERSION, images=gold_images,
                    logs=list(gold_logs.values()), requests=gold_requests, version=candidate)
            except Exception as exc:  # noqa: BLE001 - summary 에 남기고 아래에서 다시 올린다
                gold_error = exc
                gold_info["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        elapsed = time.time() - t0
        summary = {
            "run_id": run_id, "model": gemini.MODEL, "dim": args.dim, "prep_version": prep.PREP_VERSION,
            "status": status, "started_at": at, "seconds": round(elapsed, 1), "sha256_done": done,
            "sec_per_sha256": round(elapsed / max(done, 1), 4), "workers": args.workers,
            "image_batch": args.image_batch, "stats": dict(stats), "plan": plan.stats, "parts": parts,
            "skipped_sha256": skipped, "sha_mismatch_keys": sha_mismatch, "read_error_keys": read_errors,
            "api_usage": embedder.usage(),  # 경로(openrouter/google)·호출 수·OpenRouter 비용(USD)
            "gold": gold_info,  # --gold-env 일 때 쓴 환경과 행 수 (gold-migrate 가 멈추는 표시)
            "version": candidate,
        }
        s3.put_object(Bucket=bucket, Key=f"{prefix}summary.json",
                      Body=json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8"))
        print("done", status, dict(stats), f"{elapsed:.0f}s ({elapsed / max(done, 1):.3f}s/장)",
              f"-> s3://{bucket}/{prefix}", f"gold {gold_info}" if gold_info else "")
        if gold_error is not None and status == "ok":
            raise RuntimeError(f"Gold 표 쓰기 실패 (parquet·summary 는 썼음): {gold_info['error']}") from gold_error
    return _report_read_errors(read_errors, staged.read_errors, done)


def _report_read_errors(now: dict[str, str], before: dict[str, int], done: int) -> int:
    """이번 실행의 Bronze 읽기 실패를 알린다. 몇 장이면 건너뛰고 성공(0), 대부분 실패하면 EXIT_READ_ERRORS."""
    if not now:
        return 0
    print(f"Bronze 파일을 읽지 못해 건너뛴 사진 {len(now)}장 (다음 실행에서 다시 읽음):", dict(Counter(now.values())))
    stuck = sorted(k for k in now if before.get(k, 0) + 1 >= READ_ERROR_WARN_RUNS)
    if stuck:
        print(f"!! {READ_ERROR_WARN_RUNS}번 이상 연속으로 읽지 못한 Bronze 파일 {len(stuck)}개. 파일이 지워졌으면 "
              "사진 수집에서 다시 받아야 합니다:", stuck[:10])
    if len(now) >= READ_ERROR_FAIL_MIN and len(now) * 2 > done:
        print(f"!! 이번 실행에서 {len(now)}/{done} 장을 읽지 못했습니다. S3 권한·네트워크를 확인해 주세요.",
              file=sys.stderr)
        return EXIT_READ_ERRORS
    return 0


def _norm(v) -> float:
    return math.sqrt(sum(x * x for x in v))


def staged_cmd(args: argparse.Namespace) -> int:
    """S3 parquet 결과 점검 (RDS 에 넣기 전 확인용, 과금 없음)."""
    from rebom.embedding import gemini, prep, staging

    bucket, s3 = _bucket(), _s3()
    st = staging.load_staged(s3, bucket, gemini.MODEL, args.dim, prep.PREP_VERSION, run_ids=args.run_id)
    current, _ = _current(args, s3, bucket)
    rows = list(st.rows.values())
    norms = [_norm(r["embedding"]) for r in rows]
    stale = [k for k, r in st.rows.items() if (p := current.get(k)) is None or p.sha256 != r["sha256"]]
    bad_sha = [k for k, r in st.rows.items() if st.vectors.get(r["sha256"]) is None]
    _print("staged (S3 parquet)", {
        "prefix": f"s3://{bucket}/{staging.run_prefix(gemini.MODEL, args.dim)}",
        "runs": st.runs, "rows": len(rows), "unique_sha256": len(st.vectors),
        "dims": dict(Counter(len(r["embedding"]) for r in rows)),
        "norm_min_max": [round(min(norms), 4), round(max(norms), 4)] if norms else None,
        "source": dict(Counter(r["source"] for r in rows)),
        "skipped": dict(Counter(st.skipped.values())),
        "not_current_anymore": len(stale), "missing_vector": len(bad_sha),
        "current_photos": len(current), "coverage": f"{len(rows) - len(stale)}/{len(current)}",
    })
    return 0


def check_writes(write, dim: int, max_write: int) -> str | None:
    """RDS 에 쓰기 전 점검: 건수 상한, 차원, 정규화. 문제가 있으면 사유, 없으면 None."""
    if max_write and len(write) > max_write:
        return f"쓸 행 {len(write)} > 상한 {max_write} (평소 하루 수백 행. 사람이 staged 로 확인한 뒤 --max-write 0 으로 실행)"
    for p, vec in write:
        if len(vec) != dim:
            return f"{p.s3_key}: 차원 {len(vec)} != {dim}"
        if abs(_norm(vec) - 1.0) > 1e-3:
            return f"{p.s3_key}: 노름 {_norm(vec):.4f} (정규화 안 됨)"
    return None


def load(args: argparse.Namespace) -> int:
    """S3 parquet → RDS image_embedding, 변경분만. manifest 의 현재 사진 sha256 과 RDS 의 sha256 을 비교해
    새 칸과 사진이 바뀐 칸만 쓴다. staged 로 확인하고 승인받은 뒤 실행한다."""
    from rebom.embedding import gemini, plan as planmod, prep, staging, store

    bucket, s3 = _bucket(), _s3()
    gold = _gold_target(args)
    if gold is not None and args.run_id:
        sys.exit("--gold-env 와 --run-id 는 같이 쓰지 않습니다 (Gold 표는 실행별이 아니라 사진별)")
    current, _ = _current(args, s3, bucket, writes=not args.dry_run)
    if gold is None:
        st = staging.load_staged(s3, bucket, gemini.MODEL, args.dim, prep.PREP_VERSION, run_ids=args.run_id)
    else:
        st = _gold_state(gold, args.dim).staged(current)
    with store.connect() as conn:
        if not store.table_exists(conn):
            sys.exit(f"{store.TABLE} 테이블이 없습니다. db/migrations/001_init.sql 을 먼저 적용해 주세요.")
        lp = planmod.build_load(st, current, store.existing_hashes(conn, gemini.MODEL), full=args.full)
        where = f"Gold {gold[1].name}" if gold is not None else f"runs {len(st.runs)}"
        _print(f"load plan ({where}, staged rows {len(st.rows)})", lp.stats)
        if args.dry_run or not lp.write:
            return 0
        problem = check_writes(lp.write, args.dim, args.max_write)
        if problem:  # 매일 자동 적재(DAG)의 안전장치. 쓰지 않고 실패로 끝낸다
            print("load 중단:", problem, file=sys.stderr)
            return 2
        t0 = time.time()
        written = sum(store.upsert(conn, gemini.MODEL, chunk) for chunk in _chunks(lp.write, 500))
    print("loaded", written, f"{time.time() - t0:.0f}s")
    return 0


# --- smoke: RDS 에 넣은 벡터 점검 -------------------------------------------------------

def _cosine(a, b) -> float:
    return sum(x * y for x, y in zip(a, b)) / (_norm(a) * _norm(b))


def _pct(values: list[float], q: float) -> float:
    """가장 가까운 순위 방식 백분위수."""
    s = sorted(values)
    return s[min(len(s) - 1, max(0, math.ceil(q / 100 * len(s)) - 1))]


def smoke(args: argparse.Namespace) -> int:
    """Gemini 를 sample + trunc 번 부른다(소액 과금). embed 와 같은 전처리·정규화를 쓴다."""
    from rebom.embedding import gemini, prep, store

    bucket, s3 = _bucket(), _s3()
    current, _ = _current(args, s3, bucket)

    with store.connect() as conn:
        conn.read_only = True
        rows = store.stored_rows(conn, gemini.MODEL)
        if not rows:
            sys.exit("image_embedding 에 행이 없습니다. load 를 먼저 실행해 주세요.")
        norms = [_norm(r[4]) for r in rows]
        mismatch = [f"{no}/{slot}" for no, slot, sha, key, _ in rows
                    if (p := current.get((no, slot))) is None or (p.sha256, p.s3_key) != (sha, key)]
        _print("① 저장 상태", {
            "rows": len(rows),
            "dims": dict(Counter(len(r[4]) for r in rows)),
            "norm_min_max": [round(min(norms), 4), round(max(norms), 4)],
            "manifest_mismatch": len(mismatch), "mismatch_keys": mismatch[:5],
        })

        sample = store.stored_rows(conn, gemini.MODEL, limit=args.sample)
        smoke_session = "smoke-" + _new_run_id()
        embedder = gemini.GeminiEmbedder(dim=args.dim, session_id=smoke_session)
        found, sims, latency, bad = 0, [], [], []
        photos: dict[tuple[str, int], bytes] = {}
        for no, slot, sha, key, vec in sample:
            data = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            if hashlib.sha256(data).hexdigest() != sha:
                bad.append(key)
                continue
            data = prep.to_jpeg(data)  # embed 와 같은 전처리
            photos[(no, slot)] = data
            t0 = time.perf_counter()
            fresh = prep.l2_normalize(embedder.embed_image(data))  # 사진으로 질의
            top = store.nearest(conn, gemini.MODEL, fresh, k=1)
            latency.append(time.perf_counter() - t0)
            # 같은 공고의 여러 칸에 똑같은 파일이 올라온 경우(동점)가 있어 sha256 으로 판정한다
            found += bool(top) and top[0][2] == sha
            sims.append(_cosine(fresh, vec))
        _print("② 자기 자신 찾기 (사진 질의 → top-1)", {"found": f"{found}/{len(photos)}", "sha_mismatch": bad})
        _print("③ 다시 임베딩한 벡터와 저장 벡터", {
            "cosine_min": round(min(sims), 5) if sims else None,
            "cosine_mean": round(sum(sims) / len(sims), 5) if sims else None,
            "ok(>=0.99)": bool(sims) and min(sims) >= 0.99,
        })

        if args.trunc:
            full = gemini.GeminiEmbedder(dim=gemini.FULL_DIM, session_id=smoke_session)
            cos, full_dim = [], None
            for (no, slot), data in list(photos.items())[: args.trunc]:
                v_full = full.embed_image(data)
                full_dim = len(v_full)
                stored = next(r[4] for r in sample if (r[0], r[1]) == (no, slot))
                cos.append(_cosine(v_full[: args.dim], stored))  # 코사인은 크기와 무관해 정규화 전후가 같다
            _print(f"④ {gemini.FULL_DIM} 차원 앞 {args.dim} 개 vs {args.dim} 차원 출력", {
                "checked": len(cos), "full_dim": full_dim,
                "cosine_min": round(min(cos), 5) if cos else None,
                "same(>=0.99)": bool(cos) and min(cos) >= 0.99,
            })

        if latency:
            _print("⑤ 사진 질의 지연 (임베딩 호출 + pgvector 검색, 초)", {
                "n": len(latency), "p50": round(_pct(latency, 50), 3), "p95": round(_pct(latency, 95), 3),
                "max": round(max(latency), 3),
            })
    return 0


def canary_cmd(args: argparse.Namespace) -> int:
    """통과하면 0, 벡터가 바뀌었으면 3 (Airflow task 실패 → embed·load 가 돌지 않음)."""
    from rebom.embedding import canary, gemini, gold_migrate, gold_store, prep

    bucket, s3 = _bucket(), _s3()
    gold = _gold_target(args)
    if gold is not None:
        gold_store.load_logs(*gold)  # 모델을 부르기 전에 로그 표·칸을 확인
    threshold = canary.THRESHOLD if args.threshold is None else args.threshold
    if args.reset:
        archived = canary.archive_reference(s3, bucket, gemini.MODEL, args.dim)
        ref = canary.build_reference(s3, bucket, gemini.MODEL, args.dim)
        _print("canary reset", {"archived": archived, "created_at": ref["created_at"],
                                "photos": [p["sha256"][:12] for p in ref["photos"]]})
    embedder = gemini.GeminiEmbedder(dim=args.dim, session_id="canary-" + _new_run_id())
    res = canary.run(s3, bucket, embedder, gemini.MODEL, args.dim, threshold)
    _print("canary (고정 사진 → 기준 벡터 코사인)", {
        "ok": res.ok, "threshold": threshold, "min_cosine": res.min_cosine,
        "cosines": {k[:12]: v for k, v in res.cosines.items()}, "created_reference": res.created_reference,
        "api_usage": res.usage, "check": f"s3://{bucket}/{res.check_key}",
    })
    if gold is not None:  # 통과·실패 모두 남긴다
        row = gold_migrate.canary_row(res.check_key, res.check, gold_store.model_id(gemini.MODEL, args.dim))
        requests = [gold_store.request_row([sha], meta, prep.PREP_VERSION, row["gold_run_id"])
                    for sha, meta in zip(res.shas, res.requests) if meta.get("requested_at")]
        _print(f"canary → Gold {gold[1].name}", gold_store.write_canary(gold[0], gold[1], row, requests))
    if not res.ok:
        print(canary.drift_message(res, bucket, gemini.MODEL, args.dim, threshold))
        return canary.EXIT_DRIFT
    return 0


def _add_gold(p: argparse.ArgumentParser) -> None:
    p.add_argument("--gold-env", choices=("test", "prod"), default=None,
                   help="결과 원본을 Gold 임베딩 묶음 표로 (매일 DAG 는 prod). 없으면 S3 parquet 만 (전환 전 방식)")
    p.add_argument("--profile", default=None, help="Gold 카탈로그용 AWS 프로필 (PC). 파드에서는 비운다")


def _add_source(p: argparse.ArgumentParser) -> None:
    from rebom.embedding import sources

    p.add_argument("--source", choices=sources.SOURCES, default=sources.DEFAULT_SOURCE,
                   help="현재 사진을 읽는 곳. bronze: 사진 manifest (기본) / silver: Silver 이미지 표 (Silver 운영 뒤)")
    p.add_argument("--silver-env", default="test", help="--source silver 일 때 Silver 환경 (test 는 쓰기 금지)")


def compare_sources(args: argparse.Namespace) -> int:
    """Bronze manifest 와 Silver 이미지 표의 현재 사진 대조 (읽기만). 모두 0 이어야 --source silver 로 넘어간다."""
    from rebom.embedding import sources

    bucket, s3 = _bucket(), _s3()
    bronze, _ = sources.load_current("bronze", s3, bucket)
    silver = sources.load_silver(args.silver_env)
    result = sources.compare(bronze, silver)
    _print(f"bronze manifest vs silver 이미지 표 ({args.silver_env})", result)
    return 0


def gold_migrate_cmd(args: argparse.Namespace) -> int:
    """옛 parquet → Gold Iceberg 표. 기본은 계획만, --write 면 쓰고 검증 (Gemini 호출 없음)."""
    from rebom.common import gold_catalog as gc
    from rebom.embedding import gemini, gold_migrate as gm, prep, sources

    bucket, s3 = _bucket(), _s3()
    runs = gm.read_runs(s3, bucket, gemini.MODEL, args.dim)
    direct = gm.direct_runs(runs, args.gold_env)
    if direct:
        print(f"gold-migrate 멈춤: {args.gold_env} 표에 embed --gold-env 로 직접 쓴 실행이 있습니다 {direct[:3]}. "
              "전환 뒤에는 옮기기를 하지 않습니다 (DAG 가 쓴 처리 기록을 덮어씀).", file=sys.stderr)
        return 2
    current, _ = sources.load_current("bronze", s3, bucket)
    scores = gm.measure_blanks(s3, bucket, runs, current)
    plan = gm.build(runs, gemini.MODEL, args.dim, prep.PREP_VERSION,
                    gm.read_canary_docs(s3, bucket, gemini.MODEL, args.dim), scores)
    env = gc.get_gold_environment(args.gold_env)
    _print(f"gold-migrate plan → {env.name} ({env.location_prefix})", plan.stats)
    if not args.write:
        return 0
    from rebom.storage.silver.aws_catalog import get_aws_catalog

    catalog = get_aws_catalog(profile=args.profile)
    t0 = time.time()
    written = gm.write(catalog, env, plan)
    result = gm.verify(catalog, env, plan)
    _print(f"gold-migrate written {round(time.time() - t0)}s", {"written": written, "verify": result})
    return 0 if result["ok"] else 2


def embed_text(args: argparse.Namespace) -> int:
    from rebom.embedding import text_batch

    return text_batch.command(args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rebom.embedding.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    text_parser = sub.add_parser("embed-text", help="A2 카드의 새 글 벡터를 Gold에 적재")
    text_parser.add_argument("--silver-env", choices=("test", "prod"), default="test")
    text_parser.add_argument("--gold-env", choices=("test", "prod"), default="test")
    text_parser.add_argument("--profile")
    text_parser.add_argument("--version", required=True)
    text_parser.add_argument("--limit", type=int, required=True)
    text_parser.add_argument("--write", action="store_true", help="임베딩 API를 호출하고 Gold에 저장")
    text_parser.set_defaults(func=embed_text)

    d = sub.add_parser("discover", help="읽기만 하는 환경 점검")
    d.add_argument("--ping", action="store_true", help="Gemini 모델 정보 조회 (과금 없음)")
    d.add_argument("--db", action="store_true", help="RDS pgvector·테이블 상태")
    d.add_argument("--sha-sample", type=int, default=0, metavar="N", help="사진 N장을 받아 sha256 대조")
    d.set_defaults(func=discover)

    e = sub.add_parser("embed", help="사진 임베딩 → S3 parquet (+ --gold-env 면 Gold 표). RDS 는 load 로")
    e.add_argument("--limit", type=int, required=True, help="이번 실행에서 Gemini 로 보낼 최대 sha256(고유 사진) 수")
    e.add_argument("--dry-run", action="store_true", help="계획만 출력. 사진 다운로드·API·S3 쓰기 없음")
    e.add_argument("--workers", type=int, default=4, help="동시에 보내는 요청 수")
    e.add_argument("--image-batch", type=int, default=1, choices=range(1, 7), metavar="1-6",
                   help="요청 1번에 보내는 사진 수. 기본 1(요청 기록 1행 = 사진 1장). 전체 재임베딩 때만 6"),
    e.add_argument("--part-size", type=int, default=5000, help="parquet 파일 1개당 행 수")
    e.add_argument("--dim", type=int, default=768, help="벡터 차원 (테이블 vector(768) 과 같아야 함)")
    e.add_argument("--version", help="등록된 후보 버전, 예: image_embedding@1. --gold-env 필요")
    _add_source(e)
    _add_gold(e)
    e.set_defaults(func=embed)

    g = sub.add_parser("staged", help="S3 parquet 결과 점검 (과금 없음)")
    g.add_argument("--run-id", action="append", help="이 실행만 (여러 번 줄 수 있음). 없으면 전체")
    g.add_argument("--dim", type=int, default=768)
    _add_source(g)
    g.set_defaults(func=staged_cmd)

    ld = sub.add_parser("load", help="S3 parquet (또는 --gold-env 의 Gold 표) → RDS image_embedding")
    ld.add_argument("--run-id", action="append", help="이 실행만 (여러 번 줄 수 있음). 없으면 전체")
    ld.add_argument("--dry-run", action="store_true", help="변경분 수만 출력 (RDS 읽기만)")
    ld.add_argument("--full", action="store_true", help="sha256 이 같아도 다시 쓴다 (전처리·벡터를 고쳤을 때만)")
    ld.add_argument("--max-write", type=int, default=0, metavar="N",
                    help="쓸 행이 N 을 넘으면 쓰지 않고 실패 (매일 DAG 의 안전장치, 0 이면 상한 없음)")
    ld.add_argument("--dim", type=int, default=768)
    _add_source(ld)
    _add_gold(ld)
    ld.set_defaults(func=load)

    c = sub.add_parser("canary", help="매일 embed 전 모델 변화 감시: 고정 사진 6장 재임베딩(1장씩) → 기준과 코사인")
    c.add_argument("--threshold", type=float, default=None, help="통과 기준 코사인 (기본 0.99)")
    c.add_argument("--reset", action="store_true",
                   help="기준을 지금 S3 벡터로 다시 만든다(이전 기준은 보관). 모델 교체·전체 재임베딩 뒤에만")
    c.add_argument("--dim", type=int, default=768)
    _add_gold(c)
    c.set_defaults(func=canary_cmd)

    s = sub.add_parser("smoke", help="RDS 벡터 점검 (Gemini 를 sample+trunc 번 호출)")
    s.add_argument("--sample", type=int, default=10, help="다시 임베딩해 볼 저장 행 수")
    s.add_argument("--trunc", type=int, default=3, help="3072 차원으로도 받아 앞부분을 비교할 장 수 (0 이면 생략)")
    s.add_argument("--dim", type=int, default=768)
    _add_source(s)
    s.set_defaults(func=smoke)

    cs = sub.add_parser("compare-sources", help="Bronze manifest 와 Silver 이미지 표의 현재 사진 대조 (읽기만)")
    cs.add_argument("--silver-env", default="test")
    cs.set_defaults(func=compare_sources)

    gm = sub.add_parser("gold-migrate", help="옛 S3 parquet → Gold Iceberg 임베딩 표 (기본은 계획만)")
    gm.add_argument("--gold-env", required=True, choices=("test", "prod"))
    gm.add_argument("--write", action="store_true", help="표에 쓰고 다시 읽어 검증 (표는 사람이 먼저 만든다)")
    gm.add_argument("--profile", default=None, help="AWS 프로필 (PC). 파드에서는 비운다")
    gm.add_argument("--dim", type=int, default=768)
    gm.set_defaults(func=gold_migrate_cmd)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
