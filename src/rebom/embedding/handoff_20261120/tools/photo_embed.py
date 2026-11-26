"""RE:봄 사진 임베딩 (C1 확정: 사진 = Gemini Embedding 2) — 서버에서 단계별로 실행

단계 (순서대로. 앞 단계 결과를 확인한 뒤 다음 단계)
  discover  읽기만 · 비용 0   Bronze 사진 수·확장자·용량, manifest 후보, Glue Silver 테이블·칼럼, Gemini 키 읽기(길이만)
  smoke     시험 30장 · 약 $0.01   Colab 벤치마크 벡터와 같은지(코사인) · 3072→768 잘라 쓰기 · 서울 지연
  run       임베딩 → S3 parquet   --limit 1000 = 시범(약 $0.12) · --limit 0 = 전체(최대 약 $5: Bronze 41,813장 × $0.00012, 중복·빈 사진은 안 보냄) ⚠ 승인 뒤
  verify    S3 결과 확인 + 적재용 파일 만들기 · DB에는 쓰지 않음 (RDS 적재는 다음 단계의 로더로)
  compare   다른 구현(예: 저장소 CLI)이 smoke_keys.csv의 30장으로 만든 벡터를 Colab 기준과 비교 · 비용 0

환경 변수 (값은 코드·로그에 남기지 않음)
  BUCKET            (필수) 데이터 버킷 이름
  GEMINI_SECRET_ID  (smoke·run 필수) Secrets Manager 이름. 값이 JSON이면 GEMINI_API_KEY · gemini_api_key · api_key · key 중 하나를 씀
  AWS_REGION        기본 ap-northeast-2
  BRONZE_PREFIX     기본 bronze/rescue/images/          (사진 키: <prefix><공고번호>/popfile<N>.<확장자>)
  STATE_PREFIX      기본 state/embeddings/              (결과: <prefix>gemini/<RUN_ID>/...)
  SILVER_DB         기본 rebom_silver                   (discover에서 테이블 목록만 읽음)
  GEMINI_MODEL      기본 gemini-embedding-2
  GEMINI_DIM        기본 768 (벤치마크와 같음)

전처리는 벤치마크(I2)와 같음: EXIF 회전 → RGB → 긴 변 1024px → JPEG 품질 90 → 요청 1번에 6장
적재 규칙: 빈 사진(회색 256px로 줄여 픽셀 표준편차 < 4.0)은 임베딩하지 않음 · 같은 sha256은 1번만 임베딩
          개·고양이만 넣는 규칙은 verify에서 공고 정보(--notices)로 적용 (기타는 1% 미만이라 임베딩 비용 차이 약 $0.05)

사용 예
  python photo_embed.py discover
  python photo_embed.py smoke --ref s3://<BUCKET>/state/embeddings/smoke/smoke_ref.parquet
  python photo_embed.py run --run-id backfill-20261117 --limit 1000
  python photo_embed.py verify --run-id backfill-20261117 [--notices s3://.../notices.parquet]
  python photo_embed.py compare --ref smoke_ref.parquet --vectors cli_vectors.parquet     (칼럼: sha256 + vec/embedding)
"""
import argparse
import datetime as dt
import hashlib
import io
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
BUCKET = os.environ.get("BUCKET", "")
BRONZE_PREFIX = os.environ.get("BRONZE_PREFIX", "bronze/rescue/images/")
STATE_PREFIX = os.environ.get("STATE_PREFIX", "state/embeddings/")
SILVER_DB = os.environ.get("SILVER_DB", "rebom_silver")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-embedding-2")
DIM = int(os.environ.get("GEMINI_DIM", "768"))
PRICE_PER_IMAGE = 0.00012          # 유료 표준 (가격표 "$0.45 ($0.00012 per image)", 2026-11-16 확인)
PER_REQ = 6                        # 벤치마크와 같음
SIDE, JPEG_Q = 1024, 90
BLANK_STD = 4.0                    # H1c와 같음
KEY_RE = re.compile(r"(?P<no>[^/]+)/popfile(?P<slot>\d+)(?:_v(?P<ver>\d+))?\.(?P<ext>[A-Za-z0-9]+)$")   # popfile1.jpg · popfile1_v002.jpg
SECRET_FIELDS = ["GEMINI_API_KEY", "gemini_api_key", "api_key", "key"]
PHONE = re.compile(r"0\d{1,2}[-) .]?\d{3,4}[- .]?\d{4}")
PRIVATE_COL = re.compile(r"tel|phone|name|addr|nm$|owner|charge", re.I)   # 담당자·연락처·주소 칼럼은 값을 출력하지 않음


def safe_head(df, n=2):
    cols = [c for c in df.columns if not PRIVATE_COL.search(str(c)) or str(c) in ("kindNm", "upKindNm", "kind_nm")]
    return [{c: PHONE.sub("[번호]", str(v))[:60] for c, v in r.items()} for r in df[cols].head(n).to_dict("records")]


def log(*a):
    print(*a, flush=True)


def need_bucket():
    if not BUCKET:
        sys.exit("BUCKET 환경 변수가 없습니다")


# ── AWS
_clients = {}
def aws(name):
    if name not in _clients:
        import boto3
        from botocore.config import Config
        _clients[name] = boto3.client(name, region_name=REGION, config=Config(retries={"max_attempts": 8, "mode": "adaptive"},
                                                                              max_pool_connections=64))
    return _clients[name]


def s3_list(prefix, delimiter=None, limit=None):
    """키 목록 (delimiter를 주면 바로 아래 폴더 이름도 같이)"""
    out, dirs = [], []
    kw = {"Bucket": BUCKET, "Prefix": prefix}
    if delimiter:
        kw["Delimiter"] = delimiter
    for page in aws("s3").get_paginator("list_objects_v2").paginate(**kw):
        out += [{"key": o["Key"], "size": o["Size"]} for o in page.get("Contents", [])]
        dirs += [p["Prefix"] for p in page.get("CommonPrefixes", [])]
        if limit and len(out) >= limit:
            break
    return out, dirs


def s3_get(key):
    return aws("s3").get_object(Bucket=BUCKET, Key=key)["Body"].read()


def s3_put(key, data: bytes, ctype="application/octet-stream"):
    aws("s3").put_object(Bucket=BUCKET, Key=key, Body=data, ContentType=ctype)


def read_parquet_any(path):
    if path.startswith("s3://"):
        b, _, k = path[5:].partition("/")
        return pd.read_parquet(io.BytesIO(aws("s3").get_object(Bucket=b, Key=k)["Body"].read()))
    return pd.read_parquet(path)


def put_parquet(key, df):
    buf = io.BytesIO(); df.to_parquet(buf, index=False); s3_put(key, buf.getvalue())


def gemini_key():
    sid = os.environ.get("GEMINI_SECRET_ID")
    if not sid:
        sys.exit("GEMINI_SECRET_ID 환경 변수가 없습니다 (Secrets Manager 이름)")
    s = aws("secretsmanager").get_secret_value(SecretId=sid)["SecretString"]
    try:
        j = json.loads(s)
        if isinstance(j, dict):
            for f in SECRET_FIELDS:
                if j.get(f):
                    return j[f], f"JSON 필드 '{f}'"
            sys.exit(f"시크릿이 JSON인데 키 필드를 찾지 못함 (있는 필드 이름: {list(j)}) → SECRET_FIELDS에 추가")
    except json.JSONDecodeError:
        pass
    return s.strip(), "문자열"


# ── 사진
def parse_key(key):
    m = KEY_RE.search(key[len(BRONZE_PREFIX):] if key.startswith(BRONZE_PREFIX) else key)
    return (m.group("no"), int(m.group("slot")), m.group("ext").lower(), int(m.group("ver") or 1)) if m else None


def inspect(b: bytes):
    """sha256 · 크기 · 픽셀 표준편차(빈 사진 판단) · 읽기 오류"""
    from PIL import Image
    r = {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b), "w": None, "h": None, "pixel_std": None, "err": ""}
    try:
        im = Image.open(io.BytesIO(b)); r["w"], r["h"] = im.size
        g = im.convert("L"); g.thumbnail((256, 256))
        r["pixel_std"] = float(np.asarray(g, dtype=np.float32).std())
    except Exception as e:
        r["err"] = f"{type(e).__name__}"
    return r


def prep(b: bytes) -> bytes:
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(b))).convert("RGB"); im.thumbnail((SIDE, SIDE))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=JPEG_Q)
    return buf.getvalue()


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-12)


class Gemini:
    def __init__(self):
        from google import genai
        from google.genai import types
        key, how = gemini_key()
        self.types, self.client = types, genai.Client(api_key=key)
        self.n_images, self.n_calls, self.n_retry = 0, 0, 0
        log(f"Gemini 키 읽음 ({how}, 길이 {len(key)}) · 모델 {MODEL}")

    def embed(self, jpegs, dim=DIM):
        t = self.types
        contents = [t.Content(parts=[t.Part.from_bytes(data=j, mime_type="image/jpeg")]) for j in jpegs]
        cfg = t.EmbedContentConfig(output_dimensionality=dim)
        wait = 5
        for _ in range(8):
            try:
                r = self.client.models.embed_content(model=MODEL, contents=contents, config=cfg)
                self.n_calls += 1; self.n_images += len(jpegs)
                vv = [np.asarray(e.values, dtype=np.float32) for e in r.embeddings]
                if len(vv) != len(jpegs):                       # 개수가 안 맞으면 1장씩 다시
                    return [v for j in jpegs for v in self.embed([j], dim)]
                return [unit(v) for v in vv]
            except Exception as e:
                s = str(e)
                if any(x in s for x in ["429", "RESOURCE_EXHAUSTED", "500", "503", "UNAVAILABLE", "DEADLINE", "INTERNAL"]):
                    self.n_retry += 1; time.sleep(wait); wait = min(wait * 2, 120); continue
                raise
        raise RuntimeError("Gemini 한도·오류가 계속됨 → 잠시 뒤 같은 RUN_ID로 다시 실행하면 이어서 합니다")


# ── discover (읽기만)
def cmd_discover(a):
    need_bucket(); rep = {"bucket": BUCKET, "at": dt.datetime.now().isoformat(timespec="seconds")}
    log(f"① 버킷 {BUCKET} 맨 위 폴더")
    _, top = s3_list("", delimiter="/"); rep["top"] = top; log("   ", top)
    for p in ["bronze/", "silver/", "state/"]:
        _, sub = s3_list(p, delimiter="/"); rep[f"under {p}"] = sub
        log(f"   {p} 아래: {sub[:30]}")
    log(f"② 사진 {BRONZE_PREFIX}")
    objs, _ = s3_list(BRONZE_PREFIX)
    parsed = [(o, parse_key(o["key"])) for o in objs]
    ok = [(o, p) for o, p in parsed if p]
    ext = pd.Series([p[2] for _, p in ok]).value_counts().to_dict() if ok else {}
    slots = pd.Series([p[1] for _, p in ok]).value_counts().sort_index().to_dict() if ok else {}
    vers = pd.Series([p[3] for _, p in ok]).value_counts().sort_index().to_dict() if ok else {}
    rep["images"] = {"objects": len(objs), "parsed": len(ok), "other": len(objs) - len(ok), "gb": round(sum(o["size"] for o in objs) / 1e9, 2),
                     "notices": len({p[0] for _, p in ok}), "ext": ext, "slots": slots, "versions": vers, "sample": [o["key"] for o, _ in ok[:3]],
                     "other_sample": [o["key"] for o, p in parsed if not p][:5]}
    log("   ", json.dumps(rep["images"], ensure_ascii=False))
    log("③ manifest 후보 (bronze/ · state/ 아래 이름에 manifest가 들어간 파일)")
    cands = []
    for p in ["bronze/", "state/"]:
        objs2, _ = s3_list(p, limit=400000)
        cands += [o for o in objs2 if "manifest" in o["key"].lower()]
    rep["manifest_candidates"] = [c["key"] for c in cands[:50]]
    for c in cands[:5]:
        info = {"key": c["key"], "size": c["size"]}
        if c["size"] <= 50_000_000:
            try:
                b = s3_get(c["key"]); k = c["key"].lower()
                if k.endswith(".parquet"):
                    df = pd.read_parquet(io.BytesIO(b))
                elif k.endswith((".jsonl", ".ndjson")):
                    df = pd.read_json(io.BytesIO(b), lines=True)
                elif k.endswith(".json"):
                    j = json.loads(b); df = pd.json_normalize(j if isinstance(j, list) else j.get("items", j.get("records", [j])))
                elif k.endswith(".csv"):
                    df = pd.read_csv(io.BytesIO(b))
                else:
                    df = None
                if df is not None:
                    info.update(rows=len(df), columns=list(df.columns)[:40], head=safe_head(df))
            except Exception as e:
                info["read_error"] = type(e).__name__
        log("   ", json.dumps(info, ensure_ascii=False)[:1500])
        rep.setdefault("manifest_samples", []).append(info)
    log(f"④ Glue {SILVER_DB} 테이블")
    try:
        tabs = []
        for page in aws("glue").get_paginator("get_tables").paginate(DatabaseName=SILVER_DB):
            for t in page["TableList"]:
                cols = [c["Name"] for c in t.get("StorageDescriptor", {}).get("Columns", [])]
                tabs.append({"name": t["Name"], "columns": cols[:60], "location": t.get("StorageDescriptor", {}).get("Location", "")})
        rep["silver_tables"] = tabs
        for t in tabs:
            log(f"    {t['name']}: {t['columns'][:25]}")
    except Exception as e:
        rep["silver_tables_error"] = f"{type(e).__name__}: {str(e)[:200]}"; log("   읽기 실패:", rep["silver_tables_error"])
    log("⑤ Gemini 키")
    if os.environ.get("GEMINI_SECRET_ID"):
        try:
            k, how = gemini_key(); rep["gemini_key"] = {"read": True, "format": how, "length": len(k)}
            log(f"    읽음 ({how}, 길이 {len(k)}) — 값은 출력하지 않음")
            if a.ping:
                g = Gemini(); t = g.types
                r = g.client.models.embed_content(model=MODEL, contents=[t.Content(parts=[t.Part.from_text(text="ping")])],
                                                  config=t.EmbedContentConfig(output_dimensionality=DIM))
                rep["gemini_ping"] = {"dim": len(r.embeddings[0].values)}; log(f"    모델 호출 OK · {len(r.embeddings[0].values)}차원")
        except Exception as e:
            rep["gemini_key"] = {"read": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}; log("    실패:", rep["gemini_key"]["error"])
    else:
        log("    GEMINI_SECRET_ID가 없어 건너뜀")
    with open("discover_report.json", "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
    log("저장: discover_report.json (이 파일 내용을 공유하면 다음 단계 설정을 맞춤 · 키 값은 들어 있지 않음)")


# ── smoke (시험 30장)
def cmd_smoke(a):
    need_bucket()
    ref = read_parquet_any(a.ref)
    ref = ref.head(a.n).reset_index(drop=True)
    log(f"① 기준 {len(ref)}장 (Colab 벤치마크 Gemini {DIM}차원 벡터)")
    rows = []
    for r in ref.itertuples():
        objs, _ = s3_list(f"{BRONZE_PREFIX}{r.desertionNo}/")
        hit = None
        for o in objs:
            b = s3_get(o["key"])
            if hashlib.sha256(b).hexdigest() == r.sha256:
                hit = (o["key"], b); break
        p = parse_key(hit[0]) if hit else None
        rows.append({"desertionNo": r.desertionNo, "sha256": r.sha256, "found": bool(hit), "key": hit[0] if hit else "",
                     "slot_same": bool(p and p[1] == int(r.slot)), "n_objects": len(objs), "bytes": hit[1] if hit else b""})
    found = [x for x in rows if x["found"]]
    log(f"② Bronze에서 같은 sha256 찾음 {len(found)}/{len(rows)} · popfile 번호 = H1 slot {sum(x['slot_same'] for x in found)}/{len(found)}")
    if not found:
        sys.exit("Bronze에서 같은 사진을 하나도 못 찾음 → BRONZE_PREFIX나 공고 번호 형식을 discover 결과로 확인")
    g = Gemini()
    jp = [prep(x["bytes"]) for x in found]
    t_single, v768 = [], []
    for x in found:                                              # 사진 질의처럼 1장씩 (전처리 포함 시간)
        t0 = time.time(); v768.append(g.embed([prep(x["bytes"])])[0]); t_single.append(time.time() - t0)
    t0 = time.time(); v3072 = []
    for k in range(0, len(jp), PER_REQ):
        v3072 += g.embed(jp[k:k + PER_REQ], dim=3072)
    t_batch = (time.time() - t0) / len(jp)
    refv = {r.sha256: unit(np.asarray(r.vec, dtype=np.float32)) for r in ref.itertuples()}
    cos_ref = np.array([float(v @ refv[x["sha256"]]) for v, x in zip(v768, found)])
    trunc = np.array([float(unit(v3[:DIM]) @ v) for v3, v in zip(v3072, v768)])
    lat = np.array(t_single)
    rep = {"found": len(found), "n": len(rows), "slot_same": int(sum(x["slot_same"] for x in found)),
           "cos_vs_colab": {"min": float(cos_ref.min()), "mean": float(cos_ref.mean())},
           "cos_trunc3072_vs_768": {"min": float(trunc.min()), "mean": float(trunc.mean())},
           "photo_query_sec": {"p50": float(np.quantile(lat, .5)), "p95": float(np.quantile(lat, .95))},
           "batch6_sec_per_image": float(t_batch), "images_sent": g.n_images, "retries": g.n_retry,
           "cost_usd": round(g.n_images * PRICE_PER_IMAGE, 4), "model": MODEL, "dim": DIM,
           "est_full_hours_sequential": round(38691 * t_batch / 3600, 2)}
    ok1 = rep["cos_vs_colab"]["min"] >= a.min_cos
    ok2 = rep["cos_trunc3072_vs_768"]["min"] >= a.min_cos
    log(f"③ Colab 벡터와 코사인: 최소 {cos_ref.min():.4f} · 평균 {cos_ref.mean():.4f} → {'같음' if ok1 else '다름 (전처리·모델 확인)'} (기준 {a.min_cos})")
    log(f"④ 3072차원을 앞 {DIM}개로 잘라 정규화 vs {DIM}차원 요청: 최소 {trunc.min():.4f} → {'같음: 3072로 받아 두면 768·1536을 다시 호출 없이 만들 수 있음' if ok2 else '다름: 차원마다 따로 요청해야 함'}")
    log(f"⑤ 서버 지연: 사진 질의 1장(전처리 포함) p50 {rep['photo_query_sec']['p50']:.2f}초 · p95 {rep['photo_query_sec']['p95']:.2f}초 · 6장 묶음 {t_batch:.3f}초/장"
        f" → 3년치 38,691장 순서대로 약 {rep['est_full_hours_sequential']}시간")
    log(f"⑥ 보낸 사진 {g.n_images}장 · 다시 보낸 횟수 {g.n_retry} · 비용 약 ${rep['cost_usd']}")
    run = a.run_id or dt.datetime.now().strftime("smoke-%Y%m%d-%H%M%S")
    key = f"{STATE_PREFIX}smoke/{run}/report.json"
    s3_put(key, json.dumps(rep, ensure_ascii=False, indent=1).encode(), "application/json")
    with open("smoke_report.json", "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    keys = pd.DataFrame([{k: x[k] for k in ["desertionNo", "key", "sha256"]} for x in found])     # 다른 구현(CLI)으로 같은 30장을 임베딩할 때 쓰는 목록
    keys.to_csv("smoke_keys.csv", index=False)
    s3_put(f"{STATE_PREFIX}smoke/{run}/smoke_keys.csv", keys.to_csv(index=False).encode(), "text/csv")
    log(f"저장: s3://{BUCKET}/{key} · smoke_report.json · smoke_keys.csv (같은 30장의 Bronze 키)")


# ── compare (다른 구현이 만든 벡터 vs Colab 기준) — 실제로 돌아갈 코드가 벤치마크와 같은 벡터를 내는지
def cmd_compare(a):
    ref = read_parquet_any(a.ref)
    vt = read_parquet_any(a.vectors) if a.vectors.endswith(".parquet") else pd.read_csv(a.vectors)
    sc = next((c for c in ["sha256", "sha", "image_sha256", "photo_sha256"] if c in vt.columns), None)
    vc = next((c for c in ["vec", "embedding", "vector", "values"] if c in vt.columns), None)
    if not sc or not vc:
        sys.exit(f"sha256·벡터 칼럼을 찾지 못함 (있는 칼럼: {list(vt.columns)[:30]})")
    def arr(x):
        if isinstance(x, str):                                    # pgvector 문자열 "[0.1,0.2,...]"도 받음
            x = json.loads(x)
        return np.asarray(x, dtype=np.float32)
    vt = vt.drop_duplicates(sc, keep="last")
    got = {s: arr(v) for s, v in zip(vt[sc], vt[vc])}
    rows = []
    for r in ref.itertuples():
        if r.sha256 in got:
            v = got[r.sha256]; rv = np.asarray(r.vec, dtype=np.float32)
            rows.append({"sha256": r.sha256, "dim": len(v), "norm": float(np.linalg.norm(v)),
                         "cos": float(unit(v) @ unit(rv)) if len(v) == len(rv) else float("nan")})
    if not rows:
        sys.exit("기준 30장과 sha256이 겹치는 벡터가 없음 → smoke_keys.csv의 키로 임베딩했는지 확인")
    R = pd.DataFrame(rows)
    ok_dim = (R["dim"] == DIM).all(); ok_norm = np.allclose(R["norm"], 1, atol=1e-3); ok_cos = R["cos"].min() >= a.min_cos
    log(f"① 겹치는 사진 {len(R)}/{len(ref)}")
    log(f"② 차원 {sorted(R['dim'].unique().tolist())} → {'768 맞음' if ok_dim else f'{DIM}이 아님: output_dimensionality 확인'}")
    log(f"③ 노름 {R['norm'].min():.4f}~{R['norm'].max():.4f} → {'정규화됨' if ok_norm else '정규화 안 됨: 저장 전에 L2 정규화 필요'}")
    log(f"④ Colab 벡터와 코사인 최소 {R['cos'].min():.4f} · 평균 {R['cos'].mean():.4f} → "
        f"{'같음: 이 구현은 벤치마크와 같은 벡터를 냄' if ok_cos else '다름: 전처리(EXIF·RGB·1024px·JPEG 90)나 모델 이름을 확인'} (기준 {a.min_cos})")
    for r in R.nsmallest(3, "cos").itertuples():
        log(f"   낮은 순: {r.sha256[:12]} 코사인 {r.cos:.4f}")


# ── run (임베딩 → S3 parquet, 이어서 돌리기)
def run_prefix(run_id):
    return f"{STATE_PREFIX}gemini/{run_id}/"


def load_parts(run_id, kind):
    objs, _ = s3_list(run_prefix(run_id) + f"{kind}/")
    dfs = [pd.read_parquet(io.BytesIO(s3_get(o["key"]))) for o in objs if o["key"].endswith(".parquet")]
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def cmd_run(a):
    need_bucket()
    hard_cap = int(os.environ.get("MAX_IMAGES", "50000"))
    limit = a.limit if a.limit > 0 else hard_cap
    pre = run_prefix(a.run_id)
    mp, em = load_parts(a.run_id, "map"), load_parts(a.run_id, "emb")
    done_keys = set(mp.loc[mp["status"] != "embed_error", "key"]) if len(mp) else set()
    done_sha = set(em["sha256"]) if len(em) else set()
    log(f"RUN_ID {a.run_id} · 이미 처리한 사진 키 {len(done_keys):,} · 임베딩된 sha256 {len(done_sha):,} · 이번 한도 {limit:,}장")
    objs, _ = s3_list(BRONZE_PREFIX)
    todo = [o for o in objs if parse_key(o["key"]) and o["key"] not in done_keys]
    if a.order == "random":
        random.Random(a.seed).shuffle(todo)
    else:
        todo.sort(key=lambda o: o["key"])
    log(f"Bronze 사진 {len(objs):,} · 남은 키 {len(todo):,} · 순서 {a.order}")
    g = Gemini()
    n_new, t0, part = 0, time.time(), 0
    est = min(limit, len(todo)) * PRICE_PER_IMAGE
    log(f"예상 비용 최대 약 ${est:.2f} (빈 사진·중복은 보내지 않아 실제는 더 적음)")
    for k in range(0, len(todo), a.chunk):
        if n_new >= limit:
            break
        chunk = todo[k:k + a.chunk]
        with ThreadPoolExecutor(a.dl_workers) as ex:
            blobs = list(ex.map(lambda o: s3_get(o["key"]), chunk))
        maps, to_embed = [], {}
        for o, b in zip(chunk, blobs):
            no, slot, ext, ver = parse_key(o["key"])
            info = inspect(b)
            st = ("bad_image" if info["err"] else "blank" if info["pixel_std"] is not None and info["pixel_std"] < BLANK_STD
                  else "dup" if info["sha256"] in done_sha or info["sha256"] in to_embed else "ok")
            if st == "ok":
                if n_new + len(to_embed) >= limit:
                    continue                                       # 한도 → 이 키는 다음 실행에서
                to_embed[info["sha256"]] = b
            maps.append({"desertionNo": no, "slot": slot, "ver": ver, "key": o["key"], "ext": ext, "status": st, **{x: info[x] for x in ["sha256", "bytes", "w", "h", "pixel_std", "err"]}})
        jpegs = {}
        for s_, b in to_embed.items():                             # 전처리 실패(깨진 파일 등)는 bad_image
            try:
                jpegs[s_] = prep(b)
            except Exception:
                pass
        for m in maps:
            if m["status"] == "ok" and m["sha256"] not in jpegs:
                m["status"], m["err"] = "bad_image", "prep"
        shas = list(jpegs)
        groups = [shas[i:i + a.per_req] for i in range(0, len(shas), a.per_req)]
        def one(gr):
            try:
                return gr, g.embed([jpegs[s] for s in gr]), ""
            except Exception as e:
                return gr, None, f"{type(e).__name__}: {str(e)[:120]}"
        emb_rows, failed = [], set()
        with ThreadPoolExecutor(a.workers) as ex:
            for gr, vv, err in ex.map(one, groups):
                if vv is None:
                    failed |= set(gr); log("   요청 실패(다음 실행에서 다시):", err); continue
                emb_rows += [{"sha256": s, "vec": v.tolist(), "dim": DIM, "model": MODEL, "created_at": dt.datetime.now(dt.timezone.utc).isoformat()} for s, v in zip(gr, vv)]
        for m in maps:
            if m["status"] == "ok" and m["sha256"] in failed:
                m["status"] = "embed_error"
        stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S%f")
        if emb_rows:                                               # 벡터 먼저, 그다음 대응표 (중간에 끊겨도 다시 돌리면 맞음)
            put_parquet(f"{pre}emb/part-{stamp}.parquet", pd.DataFrame(emb_rows))
        if maps:
            put_parquet(f"{pre}map/part-{stamp}.parquet", pd.DataFrame(maps))
        done_sha |= {r["sha256"] for r in emb_rows}; n_new += len(emb_rows); part += 1
        el = time.time() - t0
        cnt = pd.Series([m["status"] for m in maps]).value_counts().to_dict()
        log(f"   묶음 {part} · 이번 {cnt} · 새로 임베딩 누적 {n_new:,}장 · {el / 60:.1f}분 · 장당 {el / max(n_new, 1):.3f}초 · 비용 약 ${g.n_images * PRICE_PER_IMAGE:.2f}")
    log(f"끝: 새로 임베딩 {n_new:,}장 · 요청 {g.n_calls:,}번 · 다시 보냄 {g.n_retry} · 비용 약 ${g.n_images * PRICE_PER_IMAGE:.2f} · 결과 s3://{BUCKET}/{pre}")
    log("다음: python photo_embed.py verify --run-id", a.run_id)


# ── verify (확인 + 적재용 파일, DB에는 안 씀)
def cmd_verify(a):
    need_bucket()
    pre = run_prefix(a.run_id)
    mp, em = load_parts(a.run_id, "map"), load_parts(a.run_id, "emb")
    if not len(mp):
        sys.exit("결과가 없습니다 (RUN_ID 확인)")
    mp = mp.sort_values("key").drop_duplicates("key", keep="last")
    probs = []
    em = em.drop_duplicates("sha256", keep="last")
    M = np.stack(em["vec"].map(lambda x: np.asarray(x, dtype=np.float32)).to_numpy())
    if M.shape[1] != DIM:
        probs.append(f"차원 {M.shape[1]} ≠ {DIM}")
    norms = np.linalg.norm(M, axis=1)
    if not np.isfinite(M).all() or not np.allclose(norms, 1, atol=1e-3):
        probs.append("정규화·NaN 확인 실패")
    need = set(mp.loc[mp["status"].isin(["ok", "dup"]), "sha256"])
    miss = need - set(em["sha256"])
    st = mp["status"].value_counts().to_dict()
    log(f"① 사진 키 {len(mp):,} · 상태 {st}")
    log(f"② 벡터 {len(em):,} (sha256 기준 1개씩) · {M.shape[1]}차원 · 노름 {norms.min():.4f}~{norms.max():.4f} · 벡터 없는 sha256 {len(miss)}")
    if miss:
        probs.append(f"벡터 없는 sha256 {len(miss)}개 → 같은 RUN_ID로 run을 다시 실행")
    if (mp["status"] == "embed_error").any():
        probs.append(f"embed_error {int((mp['status'] == 'embed_error').sum())}건 → run을 다시 실행하면 이 키만 다시 보냄")
    if "ver" not in mp.columns:
        mp["ver"] = 1
    n_old = len(mp)                                                  # 같은 칸(공고·slot)에 새 버전(_v002…)이 있으면 가장 새 것만
    mp = mp.sort_values("ver").drop_duplicates(["desertionNo", "slot"], keep="last")
    if n_old != len(mp):
        log(f"   새 버전이 있어 옛 버전 {n_old - len(mp):,}장은 적재에서 뺌")
    ok = mp[mp["status"].isin(["ok", "dup", "blank"])].copy()
    ok["is_blank"] = ok["status"] == "blank"
    np_ = ok.sort_values(["desertionNo", "slot"]).drop_duplicates(["desertionNo", "sha256"])          # 한 공고 안 같은 사진은 1번
    shared = np_[~np_["is_blank"]].groupby("sha256")["desertionNo"].nunique().sort_values(ascending=False)
    sus = shared[shared >= a.shared_min]
    log(f"③ 공고-사진 {len(np_):,} (한 공고 안 중복 {len(ok) - len(np_):,} 제외) · 공고 {np_['desertionNo'].nunique():,} · 빈 사진 줄 {int(np_['is_blank'].sum())}")
    log(f"④ 공고 {a.shared_min}개 이상이 같이 쓰는 사진 {len(sus)}장 → 대체 이미지일 수 있음, 사람이 확인 (shared_photos.csv)"
        f" · 빈 사진·깨진 파일 목록 blank_or_bad.csv")
    species_note = "개·고양이 필터 미적용 (--notices가 없음) → 적재 전에 공고 정보로 '기타'를 빼야 함"
    if a.notices:
        nt = read_parquet_any(a.notices) if a.notices.endswith(".parquet") else pd.read_csv(a.notices)
        col_no = next((c for c in ["desertionNo", "desertion_no"] if c in nt.columns), None)
        if col_no is None:
            sys.exit(f"--notices에 공고 번호 칼럼이 없음 (있는 칼럼: {list(nt.columns)[:30]})")
        col_sp = next((c for c in ["upKindNm", "up_kind_nm", "species", "kind_type"] if c in nt.columns), None)
        if col_sp is None:
            sys.exit(f"--notices에 종 칼럼이 없음 (있는 칼럼: {list(nt.columns)[:30]})")
        keep = set(nt.loc[nt[col_sp].astype(str).isin(["개", "고양이", "417000", "422400"]), col_no].astype(str))
        before = len(np_); np_ = np_[np_["desertionNo"].astype(str).isin(keep)]
        species_note = f"개·고양이만: {before:,} → {len(np_):,}줄 (공고 정보에 없는 공고도 빠짐)"
    log("⑤", species_note)
    em_load = em[em["sha256"].isin(set(np_.loc[~np_["is_blank"], "sha256"]))]
    model_key = f"{MODEL}:{DIM}"
    out_np = np_.assign(url=lambda x: "s3://" + BUCKET + "/" + x["key"])[["desertionNo", "slot", "sha256", "url", "is_blank"]]
    blank_list = mp[mp["status"].isin(["blank", "bad_image"])][["key", "status", "sha256", "pixel_std", "err"]]
    files = {"notice_photo.parquet": out_np, "photo_embedding__gemini.parquet": em_load[["sha256", "vec"]],
             "shared_photos.csv": sus.rename("공고 수").reset_index(), "blank_or_bad.csv": blank_list}
    man = {"model_key": model_key, "model": MODEL, "revision": "stable (모델 페이지 2026-04-28 갱신)", "dim": DIM, "modality": "image",
           "notice_photo": len(out_np), "photo_embedding": len(em_load), "run_id": a.run_id, "species_filter": species_note,
           "problems": probs, "created_at": dt.datetime.now().isoformat(timespec="seconds")}
    for name, df in files.items():
        buf = io.BytesIO()
        if name.endswith(".csv"):
            df.to_csv(buf, index=False, encoding="utf-8-sig"); s3_put(f"{pre}load/{name}", buf.getvalue(), "text/csv")
        else:
            df.to_parquet(buf, index=False); s3_put(f"{pre}load/{name}", buf.getvalue())
    s3_put(f"{pre}load/load_manifest.json", json.dumps(man, ensure_ascii=False, indent=1).encode(), "application/json")
    log(f"⑥ 적재용 파일: s3://{BUCKET}/{pre}load/ (notice_photo {len(out_np):,} · 사진 벡터 {len(em_load):,} · model_key {model_key})")
    log("확인 결과:", "문제 없음" if not probs else " · ".join(probs))
    log("※ RDS 적재는 다음 단계(모델별 키로 나눈 로더)에서, 승인 뒤")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("discover"); p.add_argument("--ping", action="store_true", help="Gemini에 글 1개를 보내 호출 확인 (비용 거의 0)")
    p = sub.add_parser("smoke"); p.add_argument("--ref", required=True); p.add_argument("--n", type=int, default=30)
    p.add_argument("--min-cos", type=float, default=0.99); p.add_argument("--run-id", default="")
    p = sub.add_parser("run"); p.add_argument("--run-id", required=True); p.add_argument("--limit", type=int, default=1000, help="새로 임베딩할 최대 장수 (0 = 전체, MAX_IMAGES까지)")
    p.add_argument("--order", choices=["random", "key"], default="random"); p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--chunk", type=int, default=300); p.add_argument("--per-req", type=int, default=PER_REQ)
    p.add_argument("--workers", type=int, default=2, help="동시에 보내는 Gemini 요청 수 (한도에 자주 걸리면 1)")
    p.add_argument("--dl-workers", type=int, default=16)
    p = sub.add_parser("compare", help="다른 구현이 만든 벡터(sha256 + vec)를 Colab 기준과 비교")
    p.add_argument("--ref", required=True); p.add_argument("--vectors", required=True); p.add_argument("--min-cos", type=float, default=0.99)
    p = sub.add_parser("verify"); p.add_argument("--run-id", required=True); p.add_argument("--notices", default="")
    p.add_argument("--shared-min", type=int, default=20)
    a = ap.parse_args()
    {"discover": cmd_discover, "smoke": cmd_smoke, "run": cmd_run, "verify": cmd_verify, "compare": cmd_compare}[a.cmd](a)


if __name__ == "__main__":
    main()
