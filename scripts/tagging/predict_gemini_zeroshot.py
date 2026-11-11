"""Gemini Embedding 2 API 제로샷으로 사진 태그 예측을 JSONL로 쓴다. EmbeddingGemma 2 와 같은 평가셋·보기 문장을 쓴다.

설치: pip install -U google-genai python-dotenv
키: 레포 루트 .env 의 GEMINI_API_KEY (유료 등급)
예)
  python scripts/tagging/predict_gemini_zeroshot.py --limit 3          # 먼저 3건으로 동작 확인
  python scripts/tagging/predict_gemini_zeroshot.py                     # 기본 평가셋 → pred_gemini_embedding2.jsonl
  python scripts/tagging/predict_gemini_zeroshot.py --eval-set data/eval/photo_tags/breed_extra.csv \
      --out data/eval/photo_tags/pred_gemini_breed_extra.jsonl
  python scripts/tagging/score.py --pred data/eval/photo_tags/pred_embeddinggemma2.jsonl data/eval/photo_tags/pred_gemini_embedding2.jsonl

보기 문장은 질의 접두어(task: search result | query: …), 사진은 접두어 없이 넣는다(Gemma 스크립트와 같은 방식).
호출 지연(초)은 같은 이름의 _timing.json 에 따로 쓴다. 비용: 사진 1장 $0.00012, 평가셋 160장이면 약 $0.02.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rebom.embedding.gemini import GeminiEmbedder, query_text  # noqa: E402
from rebom.tagging.zero_shot import PROMPT_SETS, mean_vector, zero_shot  # noqa: E402

OUT_DIR = ROOT / "data" / "eval" / "photo_tags"
METHOD = "gemini_embedding2_zeroshot"
# 검색 질의 지연 측정용 예시 문장
SAMPLE_QUERIES = ["순하고 사람 좋아하는 흰색 소형견", "서울 근처 2살 이하 고양이", "산책 좋아하는 진도 믹스", "치즈색 새끼 고양이",
                  "털 안 빠지는 작은 강아지", "대전 보호소 중형견", "얌전한 성묘", "푸들 닮은 믹스견", "검은색 대형견", "아이와 잘 지내는 개"]


def percentile(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {"n": len(values), "p50": round(statistics.median(values), 3), "p95": round(percentile(values, 0.95), 3),
            "max": round(max(values), 3)}


def encode_prompts(emb: GeminiEmbedder, prompts: dict) -> dict[str, dict[str, list[float]]]:
    out: dict[str, dict[str, list[float]]] = {}
    for tag, options in prompts.items():
        names = list(options)
        flat = [query_text(t) for o in names for t in options[o]]
        vecs = emb.embed_texts(flat)
        out[tag] = {}
        i = 0
        for o in names:
            k = len(options[o])
            out[tag][o] = mean_vector(vecs[i : i + k])
            i += k
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval-set", type=Path, default=OUT_DIR / "eval_set.csv")
    ap.add_argument("--out", type=Path, default=OUT_DIR / "pred_gemini_embedding2.jsonl")
    ap.add_argument("--dim", type=int, default=768)
    ap.add_argument("--temperature", type=float, default=0.02, help="softmax 온도. 1위 정확도에는 영향 없음")
    ap.add_argument("--prompts", choices=sorted(PROMPT_SETS), default="default", help="body = 귀·다리·꼬리")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 몇 건만 (동작 확인용)")
    args = ap.parse_args()

    with args.eval_set.open(encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r["photo_path"]]
    if args.limit:
        rows = rows[: args.limit]

    emb = GeminiEmbedder(dim=args.dim)
    t0 = time.time()
    option_vecs = encode_prompts(emb, PROMPT_SETS[args.prompts])
    print(f"보기 문장 준비 {time.time() - t0:.1f}초")

    emb.latencies.clear()
    skipped = 0
    with args.out.open("w", encoding="utf-8", newline="\n") as out:
        for i, row in enumerate(rows, 1):
            path = ROOT / row["photo_path"].replace("\\", "/")
            try:
                img = emb.embed_image(path.read_bytes())
            except (FileNotFoundError, ValueError) as exc:
                print(f"  건너뜀 {row['desertionNo']}: {exc}")
                skipped += 1
                continue
            for tag, opts in option_vecs.items():
                if tag == "breed" and row.get("upKindNm") != "개":
                    continue
                probs = zero_shot(img, opts, args.temperature)
                rec = {"desertionNo": row["desertionNo"], "photo": 1, "method": METHOD, "tag": tag,
                       "probabilities": {k: round(v, 4) for k, v in probs.items()}}
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if i % 20 == 0:
                print(f"  {i}/{len(rows)}")
    image_lat = list(emb.latencies)

    emb.latencies.clear()
    for q in SAMPLE_QUERIES:
        emb.embed_texts([query_text(q)])
    query_lat = list(emb.latencies)

    timing = {"model": "gemini-embedding-2", "dim": args.dim, "images": len(rows) - skipped, "skipped": skipped,
              "image_call_sec": summary(image_lat), "query_call_sec": summary(query_lat)}
    timing_path = args.out.with_name(args.out.stem + "_timing.json")
    timing_path.write_text(json.dumps(timing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{args.out}: 사진 {timing['images']}장 (건너뜀 {skipped})")
    print(f"지연(초) 사진 {timing['image_call_sec']} · 질의 {timing['query_call_sec']}")


if __name__ == "__main__":
    main()
