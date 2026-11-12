"""사진 인상 라인(털·얼굴) 모델 결과를 사람 답지로 채점하고 보고서를 쓴다.

예)
  python scripts/tagging/score_trait.py                                   # trait_v3 → report_trait_v3.md
  python scripts/tagging/score_trait.py --run-dir data/eval/photo_tags/trait_v1
입력: data/eval/photo_tags/trait_labels.csv (사람 답 fur·face + Claude 답 claude_fur·claude_face)
      data/eval/photo_tags/trait_v1/<모델>.run<k>.jsonl
출력: data/eval/photo_tags/report_trait_v1.md, trait_v1_detail.csv

정답은 사람 답. 사람이 '판단 어려움'으로 둔 사진은 그 항목 채점에서 뺀다.
모델이 '판단 어려움'을 고르면 틀린 것으로 센다(정확도). 채택률·채택 정확도는 따로 본다.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from math import sqrt
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rebom.tagging.trait_tags import (  # noqa: E402
    AXES,
    AXIS_NAMES,
    OPTIONS,
    UNSURE,
    average_score,
    majority,
    normalize_gold,
    score_to_option,
    to_score,
    unanimous,
)

EVAL_DIR = ROOT / "data" / "eval" / "photo_tags"
# 유료 Standard 단가, 100만 토큰당 USD (입력, 출력). score_vision.py 와 같은 값 (2026-11-11 확인)
PRICES = {"gemini-3.1-flash-lite": (0.25, 1.50), "gemini-3.5-flash": (1.50, 9.00)}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def pct(k: int, n: int) -> str:
    return f"{100 * k / n:.0f}% ({k}/{n})" if n else "-"


def load_runs(run_dir: Path) -> dict[str, list[dict]]:
    """모델 → 실행별 {desertionNo: rec} 목록 (실행 번호 순)."""
    by_model: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for p in sorted(run_dir.glob("*.run*.jsonl")):
        m = re.match(r"(.+)\.run(\d+)\.jsonl$", p.name)
        if not m:
            continue
        recs = {}
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                for ax, t in (r.get("tags") or {}).items():  # 이전 버전 결과(털 '중간')도 지금 보기로 맞춰 비교
                    if t.get("value"):
                        t["value"] = normalize_gold(ax, t["value"])
                recs[r["desertionNo"]] = r
        by_model[m.group(1)].append((int(m.group(2)), recs))
    return {k: [r for _, r in sorted(v, key=lambda x: x[0])] for k, v in by_model.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", type=Path, default=EVAL_DIR / "trait_labels.csv")
    ap.add_argument("--run-dir", type=Path, default=EVAL_DIR / "trait_v3")
    ap.add_argument("--report", type=Path, help="기본: report_<run-dir 이름>.md")
    ap.add_argument("--detail", type=Path, help="기본: <run-dir 이름>_detail.csv")
    args = ap.parse_args()
    args.report = args.report or EVAL_DIR / f"report_{args.run_dir.name}.md"
    args.detail = args.detail or EVAL_DIR / f"{args.run_dir.name}_detail.csv"

    with args.labels.open(encoding="utf-8-sig") as f:
        gold = {r["desertionNo"]: r for r in csv.DictReader(f)}
    for g in gold.values():  # 사람 답지(3단계)를 지금 보기로 맞춘다 (털 '중간' → '애매함')
        for ax in AXES:
            for key in (ax, "claude_" + ax):
                if g.get(key):
                    g[key] = normalize_gold(ax, g[key])
    models = load_runs(args.run_dir)
    lines = [f"# 사진 인상 라인 채점 ({args.run_dir.name})", "",
             f"정답: 사람 답지 {len(gold)}장. 사람이 '판단 어려움'으로 둔 사진은 그 항목에서 뺌.", ""]
    detail_rows = []

    for ax in AXES:
        name = AXIS_NAMES[ax]
        keep = [n for n, g in gold.items() if g[ax] and g[ax] != UNSURE]
        lines += [f"## {name} (채점 {len(keep)}장)", ""]
        lines.append("사람 분포: " + ", ".join(f"{o} {sum(gold[n][ax] == o for n in keep)}" for o in OPTIONS[ax][:3]))
        lines.append("")
        lines += ["| 평가자 | 정확도 | 95% 구간 | 비고 |", "|---|---|---|---|"]
        k = sum(gold[n].get("claude_" + ax) == gold[n][ax] for n in keep)
        lo, hi = wilson(k, len(keep))
        lines.append(f"| Claude (사람과 같은 화면) | {pct(k, len(keep))} | {100*lo:.0f}~{100*hi:.0f}% | 참고용 두 번째 평가자 |")

        for model, runs in models.items():
            per_run = []
            for i, recs in enumerate(runs, 1):
                kk = sum((recs.get(n, {}).get("tags") or {}).get(ax, {}).get("value") == gold[n][ax] for n in keep)
                per_run.append(kk)
                lines.append(f"| {model} 실행{i} | {pct(kk, len(keep))} | | 1회 분류 |")
            vals = {n: [((r.get(n) or {}).get("tags") or {}).get(ax, {}).get("value", "") for r in runs] for n in keep}
            vals = {n: [v for v in vs if v] for n, vs in vals.items()}
            maj = {n: majority(vs) for n, vs in vals.items()}
            k = sum(maj[n] == gold[n][ax] for n in keep)
            lo, hi = wilson(k, len(keep))
            lines.append(f"| {model} 다수결 | {pct(k, len(keep))} | {100*lo:.0f}~{100*hi:.0f}% | 동률은 오답 |")
            avg = {n: average_score(ax, vs) for n, vs in vals.items()}
            back = {n: score_to_option(ax, avg[n]) for n in keep}
            k = sum(back[n] == gold[n][ax] for n in keep)
            lo, hi = wilson(k, len(keep))
            errs = [abs(avg[n] - to_score(ax, gold[n][ax])) for n in keep if avg[n] is not None]
            mae = f"평균 점수 오차 {mean(errs):.2f}" if errs else ""
            lines.append(f"| {model} 평균 점수→보기 | {pct(k, len(keep))} | {100*lo:.0f}~{100*hi:.0f}% | {mae} |")
            una = {n: unanimous(vs) if len(vs) == len(runs) else None for n, vs in vals.items()}
            took = [n for n in keep if una[n]]
            k = sum(una[n] == gold[n][ax] for n in took)
            lines.append(f"| {model} {len(runs)}회 모두 같을 때만 | 채택 {pct(len(took), len(keep))}, 채택 중 정확 {pct(k, len(took))} | | |")
            sign_n = [n for n in keep if avg[n] is not None and to_score(ax, gold[n][ax]) != 0]
            flips = sum((avg[n] > 0 and to_score(ax, gold[n][ax]) < 0) or (avg[n] < 0 and to_score(ax, gold[n][ax]) > 0) for n in sign_n)
            lines.append(f"| {model} 반대쪽으로 틀림 | {pct(flips, len(sign_n))} | | 사람이 양끝을 고른 사진 중 모델 평균이 반대 부호 |")
            ends = OPTIONS[ax][0], OPTIONS[ax][2]
            tagged = [n for n in keep if maj[n] in ends]
            ok = sum(maj[n] == gold[n][ax] for n in tagged)
            lines.append(f"| {model} 양끝 태그 정확도 | {pct(ok, len(tagged))} | | 모델이 {ends[0]}·{ends[1]}를 고른 사진 중 사람과 같음 (태그로 보일 것) |")
            g_ends = [n for n in keep if gold[n][ax] in ends]
            hit = sum(maj[n] == gold[n][ax] for n in g_ends)
            lines.append(f"| {model} 양끝 찾은 비율 | {pct(hit, len(g_ends))} | | 사람이 {ends[0]}·{ends[1]}로 고른 사진 중 모델이 같은 태그 |")
            mids = [n for n in keep if gold[n][ax] == OPTIONS[ax][1]]
            if mids:
                held = sum(maj[n] not in ends for n in mids)
                lines.append(f"| {model} 가운데 사진에 태그 안 붙임 | {pct(held, len(mids))} | | 사람이 {OPTIONS[ax][1]}로 고른 사진 |")
            lines.append("")
            lines.append(f"{model} 다수결 혼동표 (행 사람, 열 모델)")
            lines.append("")
            cols = list(OPTIONS[ax]) + ["동률"]
            lines += ["| 사람 \\ 모델 | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
            for g in OPTIONS[ax][:3]:
                c = Counter((maj[n] or "동률") for n in keep if gold[n][ax] == g)
                lines.append(f"| {g} | " + " | ".join(str(c.get(x, 0)) for x in cols) + " |")
            lines.append("")
            for n in gold:
                vs = [((r.get(n) or {}).get("tags") or {}).get(ax, {}) for r in runs]
                detail_rows.append({
                    "desertionNo": n, "axis": ax, "model": model, "human": gold[n][ax],
                    "claude": gold[n].get("claude_" + ax, ""),
                    "runs": "|".join(v.get("value", "") for v in vs),
                    "avg_score": "" if average_score(ax, [v.get("value", "") for v in vs]) is None
                    else f"{average_score(ax, [v.get('value', '') for v in vs]):.2f}",
                    "evidence_run1": vs[0].get("evidence", "") if vs else "",
                })
        lines.append("")

    lines += ["## 비용", ""]
    for model, runs in models.items():
        u = [r["usage"] for recs in runs for r in recs.values() if "usage" in r]
        if not u:
            continue
        pin = mean(x["prompt"] for x in u)
        pout = mean(x["output"] + x["thoughts"] for x in u)
        price = PRICES.get(model)
        cost = (pin * price[0] + pout * price[1]) / 1e6 if price else None
        errs = sum("error" in r for recs in runs for r in recs.values())
        lines.append(f"- {model}: 호출당 입력 {pin:.0f}·출력(생각 포함) {pout:.0f} 토큰"
                     + (f", 호출당 ${cost:.5f}, 사진당 {len(runs)}회 ${cost * len(runs):.4f}" if cost else "")
                     + f", 오류 {errs}건")

    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    with args.detail.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(detail_rows[0]) if detail_rows else ["desertionNo"])
        w.writeheader()
        w.writerows(detail_rows)
    print(args.report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
