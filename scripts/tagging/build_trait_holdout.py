"""사진 인상 라인(털·얼굴) 질문지를 따로 확인할 새 개 사진 시트를 만든다 (trait-vision-v2 확인용).

v2 질문지는 trait_labels.csv 107장의 채점 결과를 보고 고쳤으므로, 그 107장 점수는 좋게 나오기 쉽다.
그래서 이미 쓴 공고(평가셋·몸 평가셋·품종 추가 시트)를 뺀 새 개 공고에서 무작위로 골라 사람이 따로 매긴다.
이미 받아 둔 구조 API 원본(breed_extra_source.json, 정제·개인정보 제거됨)에서 고르고 사진 1만 받는다.
품종으로 몰리지 않게 kindNm 은 보지 않고 섞는다. 보호자 이름(careOwnerNm)은 원본에 없다.

예) python scripts/tagging/build_trait_holdout.py --n 40
출력: data/eval/photo_tags/trait_holdout.csv (+ photos/<공고번호>_1.jpg)
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "tagging"))

from build_eval_set import OUT_DIR, download, rel  # noqa: E402

USED_SHEETS = ["eval_set.csv", "body_eval_set.csv", "breed_extra.csv", "trait_labels.csv"]
COLUMNS = ["desertionNo", "upKindNm", "kindNm", "photo_url", "photo_path"]


def used_ids(sheet_dir: Path) -> set[str]:
    ids: set[str] = set()
    for name in USED_SHEETS:
        p = sheet_dir / name
        if p.exists():
            with p.open(encoding="utf-8-sig") as f:
                ids |= {r["desertionNo"] for r in csv.DictReader(f)}
    return ids


def choose(records: list[dict], exclude: set[str], n: int, seed: int) -> list[dict]:
    """사진 있는 개 공고 중 안 쓴 것을 무작위로 n 건 (보호소 한 곳이 몰리지 않게 보호소당 2건까지)."""
    pool = [r for r in records if r.get("upKindNm") == "개" and r.get("popfile1") and r.get("desertionNo") not in exclude]
    random.Random(seed).shuffle(pool)
    per_shelter: dict[str, int] = {}
    out = []
    for r in pool:
        key = r.get("careRegNo") or r.get("careNm") or ""
        if per_shelter.get(key, 0) >= 2:
            continue
        per_shelter[key] = per_shelter.get(key, 0) + 1
        out.append(r)
        if len(out) >= n:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=OUT_DIR / "breed_extra_source.json")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20261112)
    ap.add_argument("--out", type=Path, default=OUT_DIR / "trait_holdout.csv")
    args = ap.parse_args()

    records = json.loads(args.source.read_text(encoding="utf-8"))
    # 사진 받기에 실패할 수 있어 여유 있게 골라 두고 n 장을 채울 때까지 받는다
    candidates = choose(records, used_ids(args.out.parent), args.n * 2, args.seed)
    photo_dir = args.out.parent / "photos"
    rows = []
    for r in candidates:
        no = r["desertionNo"]
        path = photo_dir / f"{no}_1.jpg"
        if path.exists() or download(r["popfile1"], path):
            rows.append({"desertionNo": no, "upKindNm": r.get("upKindNm", ""), "kindNm": r.get("kindNm", ""),
                         "photo_url": r["popfile1"], "photo_path": rel(path)})
        if len(rows) >= args.n:
            break
    with args.out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"원본 {len(records)}건 → {rel(args.out)}: {len(rows)}장 (후보 {len(candidates)}건)")


if __name__ == "__main__":
    main()
