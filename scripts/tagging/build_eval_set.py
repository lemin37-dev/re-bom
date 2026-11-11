"""사진 태그 평가셋(라벨 시트) 만들기.

공고 JSON 배열에서 개·고양이를 골라 라벨 시트 CSV를 만든다. 품종이 적힌 개는 닮은 품종 채점에 쓰려고 먼저 넣는다.
보호자 이름(careOwnerNm)·연락처는 시트에 넣지 않는다. 사진은 --download 로 받아 두면 같은 파일로 여러 번 돌릴 수 있다.

예)
  python scripts/tagging/build_eval_set.py                      # data/sample/rescue_sample.json, 개 50 + 고양이 50
  python scripts/tagging/build_eval_set.py --n-dog 75 --n-cat 75 --download
출력: data/eval/photo_tags/eval_set.csv (data/ 아래라 커밋되지 않음), 사진은 같은 폴더의 photos/
라벨 칸: label_fur_length(짧음/김/알 수 없음), label_injury(있음/없음/알 수 없음),
        label_photo_quality(잘 보임/가려짐·흐림/동물 없음), label_color_ratio("흰색:60, 갈색:40")
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rebom.tagging.photo_tags import colors_in_color_cd, gold_breed  # noqa: E402

DEFAULT_SOURCE = ROOT / "data" / "sample" / "rescue_sample.json"
OUT_DIR = ROOT / "data" / "eval" / "photo_tags"
COLUMNS = [
    "desertionNo", "upKindNm", "kindNm", "colorCd", "gold_breed", "colorcd_colors",
    "photo_url", "photo_path",
    "label_fur_length", "label_injury", "label_photo_quality", "label_color_ratio", "labeler", "memo",
]


def rel(path: Path) -> str:
    """레포 안이면 레포 기준 상대 경로, 밖이면 절대 경로."""
    path = path.resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def pick(records: list[dict], n_dog: int, n_cat: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    with_photo = [r for r in records if r.get("popfile1")]
    dogs = [r for r in with_photo if r.get("upKindNm") == "개"]
    cats = [r for r in with_photo if r.get("upKindNm") == "고양이"]
    breed_dogs = [r for r in dogs if gold_breed(r.get("kindNm"))]
    mixed_dogs = [r for r in dogs if not gold_breed(r.get("kindNm"))]
    rng.shuffle(breed_dogs)
    rng.shuffle(mixed_dogs)
    rng.shuffle(cats)
    chosen_dogs = (breed_dogs + mixed_dogs)[:n_dog]
    return chosen_dogs + cats[:n_cat]


def download(url: str, dest: Path, timeout: float = 20) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = resp.read()
    except Exception as exc:  # 사진 서버 오류는 건너뛰고 시트에 빈 칸으로 남긴다
        print(f"  사진 실패 {dest.name}: {exc}", file=sys.stderr)
        return False
    if len(data) < 1000:
        print(f"  사진 너무 작음(깨진 파일?) {dest.name}: {len(data)} bytes", file=sys.stderr)
        return False
    dest.write_bytes(data)
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="공고 JSON 배열")
    ap.add_argument("--n-dog", type=int, default=50)
    ap.add_argument("--n-cat", type=int, default=50)
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--out", type=Path, default=OUT_DIR / "eval_set.csv")
    ap.add_argument("--download", action="store_true", help="사진 1을 photos/ 에 받아 둔다")
    args = ap.parse_args()

    records = json.loads(args.source.read_text(encoding="utf-8"))
    rows = pick(records, args.n_dog, args.n_cat, args.seed)
    photo_dir = args.out.parent / "photos"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8-sig", newline="") as f:  # 엑셀에서 한글이 깨지지 않게 BOM
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            no = r["desertionNo"]
            path = photo_dir / f"{no}_1.jpg"
            if args.download and not path.exists():
                download(r["popfile1"], path)
            w.writerow({
                "desertionNo": no,
                "upKindNm": r.get("upKindNm", ""),
                "kindNm": r.get("kindNm", ""),
                "colorCd": r.get("colorCd", ""),
                "gold_breed": gold_breed(r.get("kindNm")) or "",
                "colorcd_colors": "|".join(sorted(colors_in_color_cd(r.get("colorCd")))),
                "photo_url": r.get("popfile1", ""),
                "photo_path": rel(path) if path.exists() else "",
            })
    n_breed = sum(1 for r in rows if gold_breed(r.get("kindNm")))
    print(f"{rel(args.out)}: {len(rows)}건 (품종 정답 있는 개 {n_breed}건)")


if __name__ == "__main__":
    main()
