"""사진 태그 예측을 라벨 시트와 대조해 채점표(마크다운)를 만든다.

예측 JSONL 한 줄 형식(방법이 달라도 같음):
  {"desertionNo": "...", "photo": 1, "method": "jev_photo", "tag": "fur_length", "probabilities": {"짧음": 0.8, ...}}
  {"desertionNo": "...", "photo": 1, "method": "color_pixel", "tag": "color_ratio", "ratios": {"흰색": 0.6, ...}}
  tag 는 fur_length · injury · photo_quality · color_main · species · breed · color_ratio

예)
  python scripts/tagging/score.py --pred data/eval/photo_tags/pred_jev.jsonl data/eval/photo_tags/pred_color_pixel.jsonl
  python scripts/tagging/score.py --pred ... --labels-b data/eval/photo_tags/eval_set_b.csv   # 라벨러 2명 일치도
출력: data/eval/photo_tags/report.md
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rebom.tagging import photo_eval as ev  # noqa: E402
from rebom.tagging.photo_tags import UNKNOWN, gold_breed, parse_ratio_label  # noqa: E402

OUT_DIR = ROOT / "data" / "eval" / "photo_tags"
LABEL_TAGS = {"fur_length": "label_fur_length", "injury": "label_injury", "photo_quality": "label_photo_quality"}


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def load_rows(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8-sig") as f:
        return {r["desertionNo"]: r for r in csv.DictReader(f)}


def load_preds(paths: list[Path]) -> dict[str, dict[str, dict[str, dict]]]:
    """method → tag → desertionNo → 레코드 (사진 1만)."""
    out: dict = defaultdict(lambda: defaultdict(dict))
    for p in paths:
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("photo", 1) == 1:
                    out[r["method"]][r["tag"]][r["desertionNo"]] = r
    return out


def gold_for(tag: str, row: dict) -> str | None:
    if tag in LABEL_TAGS:
        return row.get(LABEL_TAGS[tag]) or None
    if tag == "color_main":
        return ev.main_color(parse_ratio_label(row.get("label_color_ratio")))
    if tag == "species":
        return row.get("upKindNm") or None
    if tag == "breed":
        return gold_breed(row.get("kindNm")) or row.get("gold_breed") or None  # 별칭이 늘면 다시 맞춘다
    return None


def choice_section(tag: str, preds: dict[str, dict], rows: dict[str, dict], threshold: float) -> list[str]:
    pairs = [(gold_for(tag, row), preds.get(no, {}).get("probabilities")) for no, row in rows.items()]
    table = ev.threshold_table(pairs)
    n = table[0].n if table else 0
    lines = [f"#### {tag} (정답 있는 {n}건, macro-F1@{threshold} = {ev.macro_f1(pairs, threshold):.2f})", "",
             "| 기준 확률 | 채택률 | 채택 정확도 |", "| --- | --- | --- |"]
    lines += [f"| {r.threshold:.2f} | {pct(r.coverage)} | {pct(r.accuracy)} |" for r in table]
    lines += ["", "보정(최고 확률 구간별 실제 정답률): " + ", ".join(
        f"{name} {cnt}건 {pct(acc)}" for name, cnt, acc in ev.calibration(pairs) if cnt)]
    conf = ev.confusion(pairs, threshold)
    wrong = [(k, v) for k, v in conf.most_common() if k[0] != k[1] and k[1] != "미채택"][:5]
    if wrong:
        lines.append("자주 틀린 쌍(정답→예측): " + ", ".join(f"{g}→{p} {c}건" for (g, p), c in wrong))
    return lines + [""]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval-set", type=Path, default=OUT_DIR / "eval_set.csv")
    ap.add_argument("--pred", type=Path, nargs="+", required=True)
    ap.add_argument("--labels-b", type=Path, help="두 번째 라벨러의 시트(같은 형식)")
    ap.add_argument("--threshold", type=float, default=0.6)
    ap.add_argument("--out", type=Path, default=OUT_DIR / "report.md")
    args = ap.parse_args()

    rows = load_rows(args.eval_set)
    preds = load_preds(args.pred)
    lines = ["# 사진 태그 평가 결과", "", f"평가셋 {len(rows)}건 · 사진 1 · 기준 확률 {args.threshold}", ""]

    if args.labels_b:
        rows_b = load_rows(args.labels_b)
        lines += ["## 라벨러 일치도 (Cohen's kappa)", ""]
        for tag, col in {**LABEL_TAGS, "color_main": "label_color_ratio"}.items():
            a, b = [], []
            for no, row in rows.items():
                ga, gb = gold_for(tag, row), gold_for(tag, rows_b.get(no, {}))
                if ga and gb and UNKNOWN not in (ga, gb):
                    a.append(ga)
                    b.append(gb)
            k = ev.cohen_kappa(a, b)
            lines.append(f"- {tag}: {len(a)}건, kappa {'—' if k is None else f'{k:.2f}'}")
        lines.append("")

    for method, by_tag in sorted(preds.items()):
        lines += [f"## {method}", ""]
        for tag in ("fur_length", "injury", "photo_quality", "color_main", "species"):
            if tag in by_tag:
                lines += choice_section(tag, by_tag[tag], rows, args.threshold)
        if "breed" in by_tag:
            pairs = [(gold_for("breed", row), by_tag["breed"].get(no, {}).get("probabilities")) for no, row in rows.items()]
            s = ev.breed_summary(pairs)
            lines += [f"#### 닮은 품종 (품종이 적힌 공고 {s['n']}건)", "",
                      f"상위 1개 적중 {pct(s['top1'])} · 상위 3개 적중 {pct(s['top3'])} · "
                      f"정답이 후보 목록에 없음 {s['gold_not_in_candidates']}건", ""]
            breed_pairs = [(g, p) for g, p in pairs if g]
            lines.append("보정(1위 확률 구간별 실제 적중률): " + ", ".join(
                f"{name} {cnt}건 {pct(acc)}" for name, cnt, acc in ev.calibration(breed_pairs) if cnt))
            lines.append("")
        if "color_ratio" in by_tag:
            rp = []
            check_hits = check_n = 0
            for no, row in rows.items():
                pred = by_tag["color_ratio"].get(no, {}).get("ratios") or {}
                rp.append((parse_ratio_label(row.get("label_color_ratio")), pred))
                allowed = set(filter(None, row.get("colorcd_colors", "").split("|")))
                if pred and allowed:
                    check_n += 1
                    check_hits += ev.main_color(pred) in allowed
            s = ev.ratio_summary(rp)
            lines += [f"#### 털색 비율 (사람 라벨 {s.n}건)", "",
                      f"주색 일치 {pct(s.main_match)} · 비율 평균 오차 "
                      f"{'—' if s.mean_error_pp is None else f'{s.mean_error_pp:.1f}%p'}",
                      f"점검: 예측 주색이 공고 colorCd 에 언급된 색 안에 듦 {check_hits}/{check_n}건 ({pct(check_hits / check_n if check_n else None)})",
                      ""]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
