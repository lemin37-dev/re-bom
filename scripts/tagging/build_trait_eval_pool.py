"""사진 인상 라인(털·얼굴) 확장 평가셋을 만든다: 안 쓴 개 공고 약 1000건을 보호소 단위로 dev·test·pool 로 나눈다.

107장·40장으로는 얼굴 '늠름'이나 털 오류가 진짜 약점인지, 표본이 작아 생긴 치우침인지 가릴 수 없어 늘린다.
- dev  (기본 200): 사람이 매김. 질문지를 고칠 때만 본다.
- test (기본 300): 사람이 매김. 질문지를 정한 뒤 한 번만 채점한다.
- pool (기본 500): 사람 답 없음. 모델만 돌려 태그가 붙는 비율·비용을 본다.
같은 보호소 사진(촬영 환경이 비슷함)이 dev 와 test 에 섞이지 않게 보호소 통째로 한 묶음에 넣고,
한 보호소가 몰리지 않게 보호소당 --cap 건까지만 쓴다. kindNm 은 고르는 데 쓰지 않는다.
원본은 이미 받아 둔 breed_extra_source.json (정제·개인정보 제거됨, careOwnerNm 없음).

예) python scripts/tagging/build_trait_eval_pool.py
출력: data/eval/photo_tags/trait_pool.csv (split 열 포함, + photos/<공고번호>_1.jpg)
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "tagging"))

from build_eval_set import OUT_DIR, download, rel  # noqa: E402

USED_SHEETS = ["eval_set.csv", "body_eval_set.csv", "breed_extra.csv", "trait_labels.csv", "trait_holdout.csv"]
COLUMNS = ["desertionNo", "split", "careRegNo", "upKindNm", "kindNm", "photo_url", "photo_path"]


def used_ids(sheet_dir: Path, extra: list[str]) -> set[str]:
    ids: set[str] = set()
    for name in USED_SHEETS + extra:
        p = sheet_dir / name
        if p.exists():
            with p.open(encoding="utf-8-sig") as f:
                ids |= {r["desertionNo"] for r in csv.DictReader(f)}
    return ids


def plan_splits(records: list[dict], exclude: set[str], targets: dict[str, int], cap: int, seed: int):
    """보호소를 섞어 차례로 split 에 배정한다. 반환: [(split, 보호소키, 필요한 장수, 후보 공고 목록)]."""
    by_shelter: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if r.get("upKindNm") == "개" and r.get("popfile1") and r.get("desertionNo") not in exclude:
            by_shelter[r.get("careRegNo") or r.get("careNm") or ""].append(r)
    rng = random.Random(seed)
    shelters = sorted(by_shelter)
    rng.shuffle(shelters)
    plan, i = [], 0
    for split, target in targets.items():
        got = 0
        while got < target and i < len(shelters):
            key = shelters[i]
            i += 1
            cands = by_shelter[key][:]
            rng.shuffle(cands)
            need = min(cap, len(cands), target - got)
            plan.append((split, key, need, cands))
            got += need
    return plan


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=OUT_DIR / "breed_extra_source.json")
    ap.add_argument("--dev", type=int, default=200)
    ap.add_argument("--test", type=int, default=300)
    ap.add_argument("--pool", type=int, default=500)
    ap.add_argument("--cap", type=int, default=8, help="보호소당 최대 장수")
    ap.add_argument("--seed", type=int, default=20261112)
    ap.add_argument("--out", type=Path, default=OUT_DIR / "trait_pool.csv")
    args = ap.parse_args()

    records = json.loads(args.source.read_text(encoding="utf-8"))
    exclude = used_ids(args.out.parent, [])
    plan = plan_splits(records, exclude, {"dev": args.dev, "test": args.test, "pool": args.pool}, args.cap, args.seed)
    photo_dir = args.out.parent / "photos"
    rows, failed = [], 0
    for split, key, need, cands in plan:
        took = 0
        for r in cands:  # 사진 받기에 실패하면 같은 보호소의 다음 후보로 채운다
            if took >= need:
                break
            no = r["desertionNo"]
            path = photo_dir / f"{no}_1.jpg"
            if path.exists() or download(r["popfile1"], path):
                rows.append({"desertionNo": no, "split": split, "careRegNo": key, "upKindNm": r.get("upKindNm", ""),
                             "kindNm": r.get("kindNm", ""), "photo_url": r["popfile1"], "photo_path": rel(path)})
                took += 1
            else:
                failed += 1
    with args.out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    n = Counter(r["split"] for r in rows)
    sh = {s: len({r["careRegNo"] for r in rows if r["split"] == s}) for s in n}
    print(f"원본 {len(records)}건, 제외 {len(exclude)}건 → {rel(args.out)}: "
          + ", ".join(f"{s} {n[s]}장(보호소 {sh[s]}곳)" for s in ("dev", "test", "pool") if s in n)
          + f", 사진 실패 {failed}")


if __name__ == "__main__":
    main()
