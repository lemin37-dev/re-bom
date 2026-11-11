"""털색 비율(픽셀 방식) 예측을 JSONL로 쓴다. 사진이 받아져 있어야 한다(build_eval_set.py --download).

예) python scripts/tagging/predict_color_ratio.py                 # 가운데 70%만
    python scripts/tagging/predict_color_ratio.py --mask grabcut  # 배경 제거(opencv 필요)
출력: data/eval/photo_tags/pred_color_pixel.jsonl (grabcut 은 pred_color_grabcut.jsonl)
배경 제거에 실패한 사진은 가운데 영역 방식으로 대신 센다(fallback 필드에 표시).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rebom.tagging.color_ratio import color_ratio  # noqa: E402

METHODS = {"none": "color_pixel", "grabcut": "color_grabcut"}

OUT_DIR = ROOT / "data" / "eval" / "photo_tags"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-set", type=Path, default=OUT_DIR / "eval_set.csv")
    ap.add_argument("--out", type=Path, help="기본: pred_<method>.jsonl")
    ap.add_argument("--center", type=float, default=0.7, help="가운데 몇 %% 영역만 쓸지")
    ap.add_argument("--mask", choices=sorted(METHODS), default="none", help="배경 제거 방식")
    ap.add_argument("--margin", type=float, default=0.05, help="grabcut: 가장자리 배경 폭")
    args = ap.parse_args()
    method = METHODS[args.mask]
    args.out = args.out or OUT_DIR / f"pred_{method}.jsonl"
    if args.mask == "grabcut":
        from rebom.tagging.background import grabcut_mask

    n = fallback = 0
    with args.eval_set.open(encoding="utf-8-sig") as f, args.out.open("w", encoding="utf-8") as out:
        for row in csv.DictReader(f):
            if not row["photo_path"]:
                continue
            path = ROOT / row["photo_path"].replace("\\", "/")  # 윈도에서 만든 CSV도 읽게
            mask = grabcut_mask(path, margin=args.margin) if args.mask == "grabcut" else None
            ratios = color_ratio(path, center=args.center, mask=mask)
            rec = {"desertionNo": row["desertionNo"], "photo": 1, "method": method,
                   "tag": "color_ratio", "ratios": {k: round(v, 3) for k, v in ratios.items()}}
            if args.mask != "none" and mask is None:
                rec["fallback"] = True
                fallback += 1
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    print(f"{args.out}: {n}건" + (f" (배경 제거 실패 → 가운데 영역 {fallback}건)" if fallback else ""))


if __name__ == "__main__":
    main()
