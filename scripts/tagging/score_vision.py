"""비전 모델 귀 태그를 정답 라벨과 대조해 보고서(마크다운)와 근거 CSV 를 만든다.

예)
  python scripts/tagging/score_vision.py
  python scripts/tagging/score_vision.py --pred data/eval/photo_tags/vision/gemini-3.1-flash-lite.jsonl
  python scripts/tagging/score_vision.py --pred data/eval/photo_tags/vision/*.jsonl data/eval/photo_tags/vision_v4/*.jsonl  # 질문지 비교
출력: data/eval/photo_tags/report_vision.md, data/eval/photo_tags/vision_evidence.csv

지표 (정답 '안 보임'은 사진에 그 부위가 안 보인다는 뜻):
- 정확도: 보이는 사진 중 맞힌 비율. 모델이 '안 보임'이라 답하면 틀린 것으로 센다.
- 답한 비율 / 답한 것 정확도: 보이는 사진에서 보기를 고른 비율과 그중 맞힌 비율.
- 환각률: 안 보이는 사진에서 보기를 고른 비율 (근거 없이 채운 태그).
정답이 '판단 어려움'인 사진은 그 태그 채점에서 뺀다.
v1(귀·다리·꼬리) 결과 파일도 읽을 수 있고, OPTIONS 에 있는 태그(귀)만 채점한다. 실행 이름은 '모델 · 질문지 버전'이다.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rebom.tagging.vision_tags import (  # noqa: E402
    NOT_VISIBLE,
    OPTIONS,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    TAG_NAMES,
    UNSURE,
    USER_PROMPT,
    TagStats,
    response_schema,
)

OUT_DIR = ROOT / "data" / "eval" / "photo_tags"
# 유료 Standard 단가, 100만 토큰당 USD (입력, 출력). thinking 토큰은 출력으로 과금.
# 출처: https://ai.google.dev/gemini-api/docs/pricing (2026-11-11 확인)
PRICES = {
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.5-flash": (1.50, 9.00),
    # 3.6·3.8-flash 는 2026-12-31 까지 단가. 2027-01-01 부터 (1.50, 7.50) 으로 오른다.
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def load_gold(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8-sig") as f:
        return {r["desertionNo"]: r for r in csv.DictReader(f)}


def load_preds(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def gold_value(row: dict, tag: str) -> str:
    return (row.get(f"label_{tag}") or "").strip() or NOT_VISIBLE


def score(preds: list[dict], gold: dict[str, dict], tag: str, merge=None) -> tuple[TagStats, Counter]:
    stats, conf = TagStats(), Counter()
    for rec in preds:
        if "tags" not in rec or rec["desertionNo"] not in gold:
            continue
        g = gold_value(gold[rec["desertionNo"]], tag)
        if g == UNSURE:
            continue
        p = rec["tags"][tag]["value"]
        if merge:
            g, p = merge(g), merge(p)
        stats.add(g, p)
        conf[(g, p)] += 1
    return stats, conf


def usage_summary(model: str, preds: list[dict]) -> dict:
    ok = [r for r in preds if "tags" in r]
    if not ok:
        return {"ok": 0, "n": len(preds)}
    pin = statistics.mean(r["usage"]["prompt"] for r in ok)
    pout = statistics.mean(r["usage"]["output"] + r["usage"]["thoughts"] for r in ok)
    pth = statistics.mean(r["usage"]["thoughts"] for r in ok)
    lat = sorted(r["latency_sec"] for r in ok)
    price = PRICES.get(model)
    cost = (pin * price[0] + pout * price[1]) / 1e6 if price else None
    return {"ok": len(ok), "n": len(preds), "in": pin, "out": pout, "thoughts": pth, "cost": cost,
            "p50": statistics.median(lat), "p95": lat[min(len(lat) - 1, round(0.95 * (len(lat) - 1)))],
            "media_resolution": ok[0].get("media_resolution"), "thinking_level": ok[0].get("thinking_level")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval-set", type=Path, default=OUT_DIR / "body_eval_set.csv")
    ap.add_argument("--pred", type=Path, nargs="+", default=None, help="기본: data/eval/photo_tags/vision/*.jsonl")
    ap.add_argument("--out", type=Path, default=OUT_DIR / "report_vision.md")
    ap.add_argument("--csv", type=Path, default=OUT_DIR / "vision_evidence.csv")
    ap.add_argument("--max-examples", type=int, default=8, help="태그·모델별 틀린 사례 수")
    args = ap.parse_args()

    gold = load_gold(args.eval_set)
    paths = args.pred or sorted((OUT_DIR / "vision").glob("*.jsonl"))
    runs: dict[str, list[dict]] = {}
    for p in paths:
        preds = load_preds(p)
        version = next((r.get("prompt_version") for r in preds if r.get("prompt_version")), "?")
        runs[f"{p.stem} · {version}"] = preds

    lines = ["# 비전 모델 귀 태깅 결과", "",
             f"평가셋 {args.eval_set.name} {len(gold)}장 (개, 1번 사진) · 아래 프롬프트는 현재 질문지 {PROMPT_VERSION}", ""]

    lines += ["## 모델·설정·비용", "",
              "| 모델 | 성공 | 해상도 | thinking | 입력 토큰 | 출력 토큰 (thinking) | 사진당 비용 | 지연 p50 / p95 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for model, preds in runs.items():
        u = usage_summary(model.split(" · ")[0], preds)
        if not u["ok"]:
            lines.append(f"| {model} | 0/{u['n']} | | | | | | |")
            continue
        cost = "—" if u["cost"] is None else f"${u['cost']:.5f}"
        lines.append(f"| {model} | {u['ok']}/{u['n']} | {u['media_resolution']} | {u['thinking_level']} | {u['in']:.0f} | "
                     f"{u['out']:.0f} ({u['thoughts']:.0f}) | {cost} | {u['p50']:.2f}초 / {u['p95']:.2f}초 |")
    lines += ["", "비용 = 평균 입력 토큰 × 입력 단가 + 평균 (출력 + thinking) 토큰 × 출력 단가. "
              "단가: https://ai.google.dev/gemini-api/docs/pricing (2026-11-11)", ""]

    lines += ["## 태그별 결과", "",
              "| 모델 | 태그 | 보이는 사진 | 정확도 | 답한 비율 | 답한 것 정확도 | 안 보이는 사진 | 환각률 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    confs = {}
    for model, preds in runs.items():
        for tag in OPTIONS:
            s, conf = score(preds, gold, tag)
            confs[(model, tag)] = conf
            lines.append(f"| {model} | {TAG_NAMES[tag]} | {s.n_visible} | {pct(s.accuracy)} | {pct(s.coverage)} | "
                         f"{pct(s.precision)} | {s.n_hidden} | {pct(s.hallucination)} |")
    lines += ["", "정확도는 '안 보임'이라 답한 것도 틀린 것으로 센 값입니다. 환각률은 정답이 '안 보임'인데 보기를 고른 비율입니다.", ""]

    term_lines = []
    for model, preds in runs.items():
        counts: Counter = Counter()
        for rec in preds:
            ear = rec.get("tags", {}).get("ear", {})
            row = gold.get(rec["desertionNo"])
            if not ear.get("term") or not row:
                continue
            g = gold_value(row, "ear")
            counts[(ear["term"], "n")] += 1
            counts[(ear["term"], "ok")] += ear["value"] == g
            counts[(ear["term"], "unsure")] += g == UNSURE
        terms = dict.fromkeys(t for t, _ in counts)
        if terms:
            items = ", ".join(f"{t} {counts[(t, 'n')]}장 (정답과 같음 {counts[(t, 'ok')]})" for t in terms)
            term_lines.append(f"- {model}: {items}")
    if term_lines:
        lines += ["## 고른 견종 표준 용어 (v2 부터, 태그와 어긋난 용어는 빈 값)", "", *term_lines, ""]

    lines += ["## 혼동표 (정답 → 예측: 건수)", ""]
    for (model, tag), conf in confs.items():
        items = ", ".join(f"{g}→{p} {c}" for (g, p), c in sorted(conf.items(), key=lambda x: -x[1]))
        lines.append(f"- {model} · {TAG_NAMES[tag]}: {items}")
    lines.append("")

    lines += ["## 틀린 사례와 모델의 근거", ""]
    for model, preds in runs.items():
        lines += [f"### {model}", "", "| 공고 | 공고 품종 | 태그 | 정답 | 예측 | 근거 |", "| --- | --- | --- | --- | --- | --- |"]
        for tag in OPTIONS:
            shown = 0
            for rec in preds:
                if "tags" not in rec or shown >= args.max_examples:
                    continue
                row = gold.get(rec["desertionNo"])
                if not row:
                    continue
                g, a = gold_value(row, tag), rec["tags"][tag]
                if g != UNSURE and a["value"] != g:
                    ev = a["evidence"].replace("|", "/")
                    shown_value = f"{a['value']} ({a['term']})" if a.get("term") else a["value"]
                    lines.append(f"| {rec['desertionNo']} | {row.get('kindNm', '')} | {TAG_NAMES[tag]} | {g} | {shown_value} | {ev} |")
                    shown += 1
        lines.append("")

    lines += ["## 프롬프트", "", "### 시스템", "", "```text", SYSTEM_PROMPT, "```", "", "### 사용자 (사진 1장과 함께 전송)", "",
              "```text", USER_PROMPT, "```", "", "### 응답 스키마 (response_json_schema)", "", "```json",
              json.dumps(response_schema(), ensure_ascii=False, indent=2), "```", ""]

    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with args.csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        header = ["desertionNo", "kindNm", "tag", "gold"]
        for model in runs:
            header += [f"{model}:value", f"{model}:term", f"{model}:evidence"]
        w.writerow(header)
        by_model = {m: {r["desertionNo"]: r for r in preds} for m, preds in runs.items()}
        for no, row in gold.items():
            for tag in OPTIONS:
                out = [no, row.get("kindNm", ""), TAG_NAMES[tag], gold_value(row, tag)]
                for model in runs:
                    rec = by_model[model].get(no, {})
                    t = rec.get("tags", {}).get(tag)
                    out += [t["value"], t.get("term", ""), t["evidence"]] if t else ["", "", rec.get("error", "")]
                w.writerow(out)
    print(args.out)
    print(args.csv)


if __name__ == "__main__":
    main()
