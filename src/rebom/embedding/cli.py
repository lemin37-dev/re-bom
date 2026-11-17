"""사진 임베딩 실행 진입점. 순서: embed(S3 parquet) → staged(확인) → 승인 → load(RDS) → smoke.

    python -m rebom.embedding.cli discover [--ping] [--db] [--sha-sample N]
        읽기만 한다. Bronze 사진 수·확장자·용량, manifest 위치·칼럼, manifest↔S3 대조, Silver 테이블,
        Gemini 키(길이만), --ping 은 모델 정보 조회(과금 없음), --db 는 pgvector·테이블 상태.
    python -m rebom.embedding.cli embed --limit N [--dry-run] [--workers 4]
        manifest 의 칸별 최신 사진 중 S3 결과에 없거나 sha256 이 바뀐 것만, 같은 sha256 은 1번만 보낸다.
        sha256 이 manifest 와 다르거나 빈 사진이면 보내지 않는다. 전처리는 prep.py, 결과는 S3 parquet(staging.py).
        RDS 에는 쓰지 않는다.
    python -m rebom.embedding.cli staged [--run-id ID]
        S3 결과 점검 (과금 없음).
    python -m rebom.embedding.cli load [--run-id ID] [--dry-run] [--full]
        S3 결과 → RDS image_embedding (확인·승인 뒤). manifest 의 현재 sha256 과 RDS sha256 을 비교해 변경분만 쓴다.
    python -m rebom.embedding.cli smoke [--sample 10] [--trunc 3]
        RDS 벡터 차원·manifest 대조, 사진 질의로 자기 자신 찾기, 재임베딩 일치, 3072 앞부분 비교, 지연 p95.

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

    if args.ping and key:
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


def _new_run_id() -> str:
    import uuid

    return time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:6]


def embed(args: argparse.Namespace) -> int:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from rebom.embedding import gemini, plan as planmod, prep, staging

    bucket, s3 = _bucket(), _s3()
    staged = staging.load_staged(s3, bucket, gemini.MODEL, args.dim, prep.PREP_VERSION)
    plan = planmod.build(mf.load_manifests(s3, bucket), staged)
    order = plan.send_order()[: args.limit]
    _print("plan (보내기 전)", {
        **plan.stats,
        "this_run_sha256": len(order),
        "this_run_photos": sum(len(plan.send[s]) for s in order) + len(plan.reuse),
        "this_run_requests_max": -(-len(order) // gemini.IMAGE_BATCH),  # 빈 사진은 빠져 실제로는 더 적다
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
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    embedder = gemini.GeminiEmbedder(dim=args.dim)
    stats: Counter = Counter()
    skipped: dict[str, str] = {}
    sha_mismatch: list[str] = []
    rows: list[dict] = []
    parts: list[str] = []

    def row(p, vec, source: str) -> dict:
        return staging.photo_row(p, vec, source, run_id, prep.PREP_VERSION, at)

    def flush(final: bool = False) -> None:
        while rows and (final or len(rows) >= args.part_size):
            chunk, rows[:] = rows[: args.part_size], rows[args.part_size :]
            key = f"{prefix}part-{len(parts):05d}.parquet"
            s3.put_object(Bucket=bucket, Key=key, Body=staging.to_parquet(chunk, args.dim))
            parts.append(key)

    def prepare(sha: str):
        p = plan.send[sha][0]
        data = s3.get_object(Bucket=bucket, Key=p.s3_key)["Body"].read()
        if hashlib.sha256(data).hexdigest() != sha:
            return sha, None, "sha_mismatch"
        try:
            if prep.is_blank(data):
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
        if ready:
            vecs = embedder.embed_images([j for _, j in ready])
            out += [(sha, v, None) for (sha, _), v in zip(ready, vecs)]
        return out

    for p in plan.reuse:  # 이전 실행에서 만든 같은 sha256 벡터를 복사
        rows.append(row(p, staged.vectors[p.sha256], "reuse"))
        stats["reuse_rows"] += 1

    t0, done, status = time.time(), 0, "ok"
    next_report = 300
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(work, b) for b in _chunks(order, gemini.IMAGE_BATCH)]
            for f in as_completed(futures):
                for sha, vec, reason in f.result():
                    done += 1
                    if vec is None:
                        stats[reason.split(":")[0]] += 1
                        if reason == "sha_mismatch":  # 일시 문제일 수 있어 다음 실행에서 다시 본다
                            sha_mismatch.append(plan.send[sha][0].s3_key)
                        else:  # 빈 사진·열리지 않는 파일은 다음 실행에서도 보내지 않는다
                            skipped[sha] = reason
                        continue
                    vec = prep.l2_normalize(vec)
                    if len(vec) != args.dim:
                        raise RuntimeError(f"벡터 차원 {len(vec)} != {args.dim}")
                    stats["api_sha256"] += 1
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
        elapsed = time.time() - t0
        summary = {
            "run_id": run_id, "model": gemini.MODEL, "dim": args.dim, "prep_version": prep.PREP_VERSION,
            "status": status, "started_at": at, "seconds": round(elapsed, 1), "sha256_done": done,
            "sec_per_sha256": round(elapsed / max(done, 1), 4), "workers": args.workers,
            "image_batch": gemini.IMAGE_BATCH, "stats": dict(stats), "plan": plan.stats, "parts": parts,
            "skipped_sha256": skipped, "sha_mismatch_keys": sha_mismatch,
        }
        s3.put_object(Bucket=bucket, Key=f"{prefix}summary.json",
                      Body=json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8"))
        print("done", status, dict(stats), f"{elapsed:.0f}s ({elapsed / max(done, 1):.3f}s/장)",
              f"-> s3://{bucket}/{prefix}")
    return 0


def _norm(v) -> float:
    return math.sqrt(sum(x * x for x in v))


def staged_cmd(args: argparse.Namespace) -> int:
    """S3 parquet 결과 점검 (RDS 에 넣기 전 확인용, 과금 없음)."""
    from rebom.embedding import gemini, prep, staging

    bucket, s3 = _bucket(), _s3()
    st = staging.load_staged(s3, bucket, gemini.MODEL, args.dim, prep.PREP_VERSION, run_ids=args.run_id)
    current = mf.latest_photos(mf.load_manifests(s3, bucket))
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


def load(args: argparse.Namespace) -> int:
    """S3 parquet → RDS image_embedding, 변경분만. manifest 의 현재 사진 sha256 과 RDS 의 sha256 을 비교해
    새 칸과 사진이 바뀐 칸만 쓴다. staged 로 확인하고 승인받은 뒤 실행한다."""
    from rebom.embedding import gemini, plan as planmod, prep, staging, store

    bucket, s3 = _bucket(), _s3()
    st = staging.load_staged(s3, bucket, gemini.MODEL, args.dim, prep.PREP_VERSION, run_ids=args.run_id)
    current = mf.latest_photos(mf.load_manifests(s3, bucket))
    with store.connect() as conn:
        if not store.table_exists(conn):
            sys.exit(f"{store.TABLE} 테이블이 없습니다. db/migrations/001_init.sql 을 먼저 적용해 주세요.")
        lp = planmod.build_load(st, current, store.existing_hashes(conn, gemini.MODEL), full=args.full)
        _print(f"load plan (runs {len(st.runs)}, staged rows {len(st.rows)})", lp.stats)
        if args.dry_run or not lp.write:
            return 0
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
    current = mf.latest_photos(mf.load_manifests(s3, bucket))

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
        embedder = gemini.GeminiEmbedder(dim=args.dim)
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
            full = gemini.GeminiEmbedder(dim=gemini.FULL_DIM)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rebom.embedding.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="읽기만 하는 환경 점검")
    d.add_argument("--ping", action="store_true", help="Gemini 모델 정보 조회 (과금 없음)")
    d.add_argument("--db", action="store_true", help="RDS pgvector·테이블 상태")
    d.add_argument("--sha-sample", type=int, default=0, metavar="N", help="사진 N장을 받아 sha256 대조")
    d.set_defaults(func=discover)

    e = sub.add_parser("embed", help="사진 임베딩 → S3 parquet (RDS 는 load 로)")
    e.add_argument("--limit", type=int, required=True, help="이번 실행에서 Gemini 로 보낼 최대 sha256(고유 사진) 수")
    e.add_argument("--dry-run", action="store_true", help="계획만 출력. 사진 다운로드·API·S3 쓰기 없음")
    e.add_argument("--workers", type=int, default=4, help="동시에 보내는 요청 수 (요청 1번 = 사진 최대 6장)")
    e.add_argument("--part-size", type=int, default=5000, help="parquet 파일 1개당 행 수")
    e.add_argument("--dim", type=int, default=768, help="벡터 차원 (테이블 vector(768) 과 같아야 함)")
    e.set_defaults(func=embed)

    g = sub.add_parser("staged", help="S3 parquet 결과 점검 (과금 없음)")
    g.add_argument("--run-id", action="append", help="이 실행만 (여러 번 줄 수 있음). 없으면 전체")
    g.add_argument("--dim", type=int, default=768)
    g.set_defaults(func=staged_cmd)

    ld = sub.add_parser("load", help="S3 parquet → RDS image_embedding (확인·승인 뒤)")
    ld.add_argument("--run-id", action="append", help="이 실행만 (여러 번 줄 수 있음). 없으면 전체")
    ld.add_argument("--dry-run", action="store_true", help="변경분 수만 출력 (RDS 읽기만)")
    ld.add_argument("--full", action="store_true", help="sha256 이 같아도 다시 쓴다 (전처리·벡터를 고쳤을 때만)")
    ld.add_argument("--dim", type=int, default=768)
    ld.set_defaults(func=load)

    s = sub.add_parser("smoke", help="RDS 벡터 점검 (Gemini 를 sample+trunc 번 호출)")
    s.add_argument("--sample", type=int, default=10, help="다시 임베딩해 볼 저장 행 수")
    s.add_argument("--trunc", type=int, default=3, help="3072 차원으로도 받아 앞부분을 비교할 장 수 (0 이면 생략)")
    s.add_argument("--dim", type=int, default=768)
    s.set_defaults(func=smoke)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
