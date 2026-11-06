"""닮은 품종 채점용 '품종이 적힌 개' 공고를 더 모아 별도 시트로 만든다.

샘플(data/sample/rescue_sample.json)의 품종견 26건은 이미 평가셋에 다 들어가서, 구조 API에서 새로 받는다.
품종 정답은 공고 kindNm 에서 오므로 손 라벨이 필요 없다. 한 품종이 몰리지 않게 품종마다 --per-breed 건까지만 넣는다.
인증키는 .env 의 DATA_GO_KR_SERVICE_KEY 에서만 읽고, 보호자 이름(careOwnerNm)은 저장하지 않는다.

예) python scripts/tagging/fetch_breed_dogs.py --download
    python scripts/tagging/predict_embedding_zeroshot.py --eval-set data/eval/photo_tags/breed_extra.csv --out data/eval/photo_tags/pred_breed_extra.jsonl
    python scripts/tagging/score.py --eval-set data/eval/photo_tags/breed_extra.csv --pred data/eval/photo_tags/pred_breed_extra.jsonl --out data/eval/photo_tags/report_breed_extra.md
출력: data/eval/photo_tags/breed_extra.csv (+ photos/), 받은 원본은 breed_extra_source.json (data/ 아래라 커밋되지 않음)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "rescue"))
sys.path.insert(0, str(ROOT / "scripts" / "tagging"))

from build_eval_set import COLUMNS, OUT_DIR, download, rel  # noqa: E402
from rebom.tagging.photo_tags import colors_in_color_cd, gold_breed  # noqa: E402
from sanitize import sanitize  # noqa: E402

DOG = "417000"  # 구조 API upkind: 개


def choose(records: list[dict], exclude: set[str], target: int, per_breed: int, seed: int) -> list[dict]:
    """사진 있고 품종 정답 있는 개를 품종마다 per_breed 건까지, 모두 target 건까지 고른다."""
    pool = [r for r in records
            if r.get("popfile1") and gold_breed(r.get("kindNm")) and r.get("desertionNo") not in exclude]
    random.Random(seed).shuffle(pool)
    taken: Counter[str] = Counter()
    out = []
    for r in pool:
        b = gold_breed(r.get("kindNm"))
        if taken[b] >= per_breed:
            continue
        taken[b] += 1
        out.append(r)
        if len(out) >= target:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=60, help="발생일 기준 최근 며칠")
    ap.add_argument("--target", type=int, default=60)
    ap.add_argument("--per-breed", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20261103)
    ap.add_argument("--exclude", type=Path, default=OUT_DIR / "eval_set.csv", help="이미 쓴 공고 시트")
    ap.add_argument("--out", type=Path, default=OUT_DIR / "breed_extra.csv")
    ap.add_argument("--download", action="store_true", help="사진 1을 photos/ 에 받아 둔다")
    args = ap.parse_args()

    from fetch_snapshot import fetch  # API 키·requests 가 필요해서 실행할 때만 부른다

    today = dt.date.today()
    params = {"upkind": DOG, "bgnde": (today - dt.timedelta(days=args.days)).strftime("%Y%m%d"),
              "endde": today.strftime("%Y%m%d")}
    records = [sanitize(r) for r in fetch(params).values()]
    source = args.out.with_name(args.out.stem + "_source.json")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")

    exclude = set()
    if args.exclude.exists():
        with args.exclude.open(encoding="utf-8-sig") as f:
            exclude = {r["desertionNo"] for r in csv.DictReader(f)}
    rows = choose(records, exclude, args.target, args.per_breed, args.seed)

    photo_dir = args.out.parent / "photos"
    with args.out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            no = r["desertionNo"]
            path = photo_dir / f"{no}_1.jpg"
            if args.download and not path.exists():
                download(r["popfile1"], path)
            w.writerow({
                "desertionNo": no, "upKindNm": r.get("upKindNm", ""), "kindNm": r.get("kindNm", ""),
                "colorCd": r.get("colorCd", ""), "gold_breed": gold_breed(r.get("kindNm")) or "",
                "colorcd_colors": "|".join(sorted(colors_in_color_cd(r.get("colorCd")))),
                "photo_url": r.get("popfile1", ""), "photo_path": rel(path) if path.exists() else "",
            })
    breeds = Counter(gold_breed(r.get("kindNm")) for r in rows)
    print(f"받은 개 공고 {len(records)}건 → {rel(args.out)}: {len(rows)}건, 품종 {len(breeds)}종")
    print("  " + ", ".join(f"{b} {n}" for b, n in breeds.most_common()))


if __name__ == "__main__":
    main()
