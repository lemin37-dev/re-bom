"""EmbeddingGemma 2 제로샷으로 사진 태그 예측을 JSONL로 쓴다. 로컬(인터넷으로 모델을 받을 수 있는 곳)에서 돌린다.

설치: pip install -U sentence-transformers transformers torch pillow
예)
  python scripts/tagging/predict_embedding_zeroshot.py --limit 5     # 먼저 5건으로 동작 확인
  python scripts/tagging/predict_embedding_zeroshot.py
출력: data/eval/photo_tags/pred_embeddinggemma2.jsonl → score.py --pred 에 넣는다.

사진 입력 방식은 모델 카드의 예시({"text": "<|image|>", "image": [...]})를 따랐다. 카드에 사진만 넣는 예시는
없어서, 오류가 나면 모델 카드의 최신 사용법을 확인해 encode_image() 만 고치면 된다.
문장 쪽은 검색 질의 접두어(prompt_name="SearchQuery"), 사진은 접두어 없이 넣는다. float16 은 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rebom.tagging.zero_shot import PROMPTS, mean_vector, zero_shot  # noqa: E402

OUT_DIR = ROOT / "data" / "eval" / "photo_tags"
MODEL_ID = "google/embeddinggemma-2"


def load_model():
    import torch
    from sentence_transformers import SentenceTransformer

    if torch.cuda.is_available():
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float32
    else:
        dtype = torch.float32
    return SentenceTransformer(MODEL_ID, model_kwargs={"torch_dtype": dtype})


def encode_image(model, path: Path) -> list[float]:
    vec = model.encode({"text": "<|image|>", "image": [str(path)]})
    return [float(x) for x in vec]


def encode_prompts(model) -> dict[str, dict[str, list[float]]]:
    out: dict[str, dict[str, list[float]]] = {}
    for tag, options in PROMPTS.items():
        out[tag] = {}
        for option, texts in options.items():
            vecs = model.encode(texts, prompt_name="SearchQuery")
            out[tag][option] = mean_vector([[float(x) for x in v] for v in vecs])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval-set", type=Path, default=OUT_DIR / "eval_set.csv")
    ap.add_argument("--out", type=Path, default=OUT_DIR / "pred_embeddinggemma2.jsonl")
    ap.add_argument("--temperature", type=float, default=0.02, help="softmax 온도. 1위 정확도에는 영향 없음")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 몇 건만 (동작 확인용)")
    args = ap.parse_args()

    with args.eval_set.open(encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r["photo_path"]]
    if args.limit:
        rows = rows[: args.limit]

    t0 = time.time()
    model = load_model()
    option_vecs = encode_prompts(model)
    print(f"모델·문장 준비 {time.time() - t0:.0f}초")

    t1 = time.time()
    with args.out.open("w", encoding="utf-8") as out:
        for i, row in enumerate(rows, 1):
            img = encode_image(model, ROOT / row["photo_path"].replace("\\", "/"))
            for tag, opts in option_vecs.items():
                if tag == "breed" and row.get("upKindNm") != "개":
                    continue
                probs = zero_shot(img, opts, args.temperature)
                rec = {"desertionNo": row["desertionNo"], "photo": 1, "method": "embeddinggemma2_zeroshot",
                       "tag": tag, "probabilities": {k: round(v, 4) for k, v in probs.items()}}
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if i % 10 == 0:
                print(f"  {i}/{len(rows)}")
    n = len(rows)
    print(f"{args.out}: {n}건, 사진 1장당 {(time.time() - t1) / max(n, 1):.2f}초")


if __name__ == "__main__":
    main()
