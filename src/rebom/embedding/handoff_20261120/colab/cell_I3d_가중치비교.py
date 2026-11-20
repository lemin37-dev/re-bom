# ── 칸 I3d. 글:사진 가중치 비교 (I2 벡터만 읽음 · CPU 1분 · API 안 씀)
#    11-18: 사진·글 모두 Gemini(B)로 바꿨으므로 TEXT_M = "gemini"로 다시 잼 (11-17 값은 C1: 글 EG2)
#    목적: ① 기본 가중치 정하기 ② 토글('생김새 중심' / '특징 중심')이 실제로 다른 결과를 내는지 확인
#    질의: S3 중 성격 조건(온순·활발·사람 좋아) 102개 — 성별·중성화·나이는 필터로 처리하므로 글·사진 비중이 의미 있는 질의만
#    점수: 가중 RRF = w글/(60 + 글 순위) + w사진/(60 + 사진 순위)   (0.5/0.5 = I3c의 RRF와 같은 순서)
#    지표: P@10 생김새+조건 · 생김새만 · 조건만 · MRR — 실제 품종견 비율로 맞춤 + 0.5/0.5와의 짝 비교
#    읽는 법: 사진 비중을 올리면 '생김새만'이 오르고 '조건만'이 내려가야 정상. 두 값이 거의 안 변하면 토글은 의미가 작음
#    결과: bench1k/results1k_weights.csv
import json, hashlib, pathlib
import numpy as np
import pandas as pd
try:
    from google.colab import drive
    drive.mount("/content/drive")
except Exception:
    pass

DS_DIR = pathlib.Path("/content/drive/MyDrive/rebom_out/dataset_v2")
H_DIR = DS_DIR / "full_3y"; B_DIR = DS_DIR / "bench1k"; VEC = B_DIR / "vecs"
PHOTO_M, TEXT_M = "gemini", "gemini"                                 # B (11-18) · C1이면 TEXT_M = "eg2"
WEIGHTS = [(1.0, 0.0), (0.8, 0.2), (0.7, 0.3), (0.65, 0.35), (0.6, 0.4), (0.55, 0.45), (0.5, 0.5), (0.4, 0.6), (0.3, 0.7), (0.0, 1.0)]   # (글, 사진)
BASE = (0.5, 0.5)                                                    # 짝 비교 기준 (첫 화면 후보로 바꿔도 됨, 예: (0.6, 0.4))
OVERLAP_PAIRS = [((0.6, 0.4), (0.7, 0.3)), ((0.6, 0.4), (0.8, 0.2)), ((0.6, 0.4), (0.4, 0.6)), ((0.7, 0.3), (0.4, 0.6)), ((0.8, 0.2), (0.4, 0.6))]
FILTER_CONDS = ("수컷인", "암컷인", "중성화한", "년생 어린")
W_PURE = None
RRF_K, N_BOOT, SEED = 60, 2000, 17
rng = np.random.default_rng(SEED)
print(f"I3d 11-18판 · 사진 {PHOTO_M} · 글 {TEXT_M}")

d = pd.read_parquet(B_DIR / "dogs1k.parquet")
photos = pd.read_parquet(B_DIR / "photos1k.parquet")
Q = pd.read_parquet(B_DIR / "queries1k.parquet")
meta = json.loads((B_DIR / "meta1k.json").read_text(encoding="utf-8"))
ids = d["desertionNo"].astype(str).to_numpy(); row = {a: i for i, a in enumerate(ids)}
pi = photos["desertionNo"].astype(str).map(row).to_numpy(); shas = photos["sha256"].to_numpy()
pure = d["pure"].astype(bool).to_numpy()

def tkey(kind_, t):
    return f"{kind_}:" + hashlib.sha1(t.encode("utf-8")).hexdigest()

def unit_rows(M):
    M = np.asarray(M, dtype=np.float32)
    return M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-12)

def load(m):
    t = pd.read_parquet(VEC / f"{m}.parquet")
    return {k: np.asarray(x, dtype=np.float32) for k, x in zip(t["key"], t["vec"])}
SP, STX = load(PHOTO_M), load(TEXT_M)
P = unit_rows([SP[f"img:{s}"] for s in shas])
D = unit_rows([STX[tkey("doc", t)] for t in d["text"]])

nb = pd.read_parquet(H_DIR / "notices_3y.parquet", columns=["desertionNo", "upKindNm", "kindNm", "happenDt"]).drop_duplicates("desertionNo")
dg = nb[nb["upKindNm"] == "개"]; kn = dg["kindNm"].fillna("").astype(str).str.strip()
hd = pd.to_datetime(dg["happenDt"].astype(str), format="%Y%m%d", errors="coerce")
win = (hd > pd.Timestamp(meta["window"][0])) & (hd <= pd.Timestamp(meta["window"][1]))
w = float(W_PURE) if W_PURE is not None else float((~(kn.str.contains("믹스") | kn.isin(["", "기타", "미상"])))[win].mean())

def sboot(x, g):
    x, g = np.asarray(x, float), np.asarray(g, bool)
    xp, xm = x[g], x[~g]
    if not len(xp) or not len(xm):
        b = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(N_BOOT)]
        return float(x.mean()), float(np.quantile(b, .025)), float(np.quantile(b, .975))
    b = [w * xp[rng.integers(0, len(xp), len(xp))].mean() + (1 - w) * xm[rng.integers(0, len(xm), len(xm))].mean() for _ in range(N_BOOT)]
    return float(w * xp.mean() + (1 - w) * xm.mean()), float(np.quantile(b, .025)), float(np.quantile(b, .975))

f3 = lambda t: f"{t[0]:.3f} ({t[1]:.3f}~{t[2]:.3f})"                          # noqa: E731
fd = lambda t: f"{t[0]:+.3f} ({t[1]:+.3f}~{t[2]:+.3f})"                       # noqa: E731
verdict = lambda t: "나음" if t[1] > 0 else ("나쁨" if t[2] < 0 else "차이 없음")  # noqa: E731

def rank_of(x):
    r = np.empty(len(x), int); r[np.argsort(-x)] = np.arange(1, len(x) + 1)
    return r

s3 = Q[Q["qtype"] == "S3"].reset_index(drop=True)
s3 = s3[[not any(k in c for k in FILTER_CONDS) for c in s3["cond"]]].reset_index(drop=True)
qpure = np.array([pure[row[a]] for a in s3["qdog"]])
print(f"성격 조건 S3 {len(s3)}개 (조건별 {s3['cond'].value_counts().to_dict()}) · 품종견 질의 {int(qpure.sum())} · 실제 품종견 비율 {w:.1%}로 맞춤")

R = {wt: {"both": [], "look": [], "cond": [], "mrr": []} for wt in WEIGHTS}
for _, r in s3.iterrows():
    excl = [row[a] for a in r["exclude"] if a in row]
    s = P @ P[pi == row[r["qdog"]]].mean(0); per = np.full(len(d), -np.inf); np.maximum.at(per, pi, s)
    txt = D @ STX[tkey("query", r["text"])]
    per[excl] = -np.inf; txt[excl] = -np.inf
    ok = np.isfinite(per)
    rp, rt = rank_of(np.where(ok, per, -1e9)), rank_of(np.where(ok, txt, -1e9))
    rel, look, cok = ({row[a] for a in r[c] if a in row} for c in ["rel", "look", "cond_ok"])
    for wt in WEIGHTS:
        sc = np.where(ok, wt[0] / (RRF_K + rt) + wt[1] / (RRF_K + rp), -1e9)
        top = np.argsort(-sc)[:10]; h = np.array([j in rel for j in top])
        R[wt]["both"].append(h.mean()); R[wt]["mrr"].append(1 / (np.argmax(h) + 1) if h.any() else 0.0)
        R[wt]["look"].append(np.mean([j in look for j in top])); R[wt]["cond"].append(np.mean([j in cok for j in top]))
R = {wt: {k: np.array(v) for k, v in m.items()} for wt, m in R.items()}

name = lambda wt: "글만" if wt[1] == 0 else ("사진만" if wt[0] == 0 else f"글 {wt[0]:g} · 사진 {wt[1]:g}")   # noqa: E731
base = BASE
t1 = []
for wt in WEIGHTS:
    m = R[wt]
    dl = sboot(m["both"] - R[base]["both"], qpure)
    t1.append({"가중치": name(wt), "P@10 생김새+조건": f3(sboot(m["both"], qpure)), "생김새만": f"{sboot(m['look'], qpure)[0]:.3f}",
               "조건만": f"{sboot(m['cond'], qpure)[0]:.3f}", "MRR": f"{sboot(m['mrr'], qpure)[0]:.3f}",
               f"{name(base)} 대비": "기준" if wt == base else fd(dl), "판정": "" if wt == base else verdict(dl)})
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
SYS = "B: Gemini만" if TEXT_M == "gemini" else "C1: Gemini 사진 + EG2 글"
print(f"\n■ 가중치별 결과 ({SYS}, 실제 비율로 맞춤, 괄호 = 95% 구간)")
print(pd.DataFrame(t1).to_string(index=False))
print(f"   ※ 글 0.5 · 사진 0.5는 I3c '성격 조건 (사진+글)'의 {SYS.split(':')[0]} 값과 같아야 함 (같은 순서: C1 0.338 · B 0.361)")

t2 = []
for c in s3["cond"].unique():
    msk = (s3["cond"] == c).to_numpy()
    t2.append({"조건": c, "n": int(msk.sum()), **{name(wt): round(float(R[wt]["both"][msk].mean()), 3) for wt in WEIGHTS}})
print("\n■ 조건별 P@10 생김새+조건 (평가셋 그대로)")
print(pd.DataFrame(t2).to_string(index=False))

# 토글 후보끼리 서로 얼마나 다른 결과를 내는지 (상위 10마리 겹침) · OVERLAP_PAIRS에서 바꿈
print("\n■ 토글 후보끼리 상위 10마리가 얼마나 겹치나 (1.0 = 같은 결과)")
for a, b in OVERLAP_PAIRS:
    ov = []
    for _, r in s3.iterrows():
        excl = [row[x] for x in r["exclude"] if x in row]
        s = P @ P[pi == row[r["qdog"]]].mean(0); per = np.full(len(d), -np.inf); np.maximum.at(per, pi, s)
        txt = D @ STX[tkey("query", r["text"])]; per[excl] = -np.inf; txt[excl] = -np.inf
        ok = np.isfinite(per); rp, rt = rank_of(np.where(ok, per, -1e9)), rank_of(np.where(ok, txt, -1e9))
        ta = set(np.argsort(-np.where(ok, a[0] / (RRF_K + rt) + a[1] / (RRF_K + rp), -1e9))[:10])
        tb = set(np.argsort(-np.where(ok, b[0] / (RRF_K + rt) + b[1] / (RRF_K + rp), -1e9))[:10])
        ov.append(len(ta & tb) / 10)
    print(f"   {name(a)} vs {name(b)}: 평균 {np.mean(ov):.2f}")
print("   → 겹침이 0.9 이상이면 토글을 눌러도 화면이 거의 안 바뀜 (가중치 차이를 더 벌려야 함)")

pd.DataFrame([{"가중치": name(wt), **{k: float(sboot(v, qpure)[0]) for k, v in m.items()}} for wt, m in R.items()]).to_csv(
    B_DIR / "results1k_weights.csv", index=False, encoding="utf-8-sig")
print("\n저장:", B_DIR / "results1k_weights.csv")
