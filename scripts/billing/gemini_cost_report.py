"""Gemini API 사용 금액을 모델별로 모아 HTML 리포트로 만든다.

데이터: Cloud Billing '표준 사용 비용' BigQuery 내보내기 테이블 (결제 계정 단위라 팀원 누구의 호출이든 함께 잡힌다).
인증: 각자 `gcloud auth application-default login` (키 파일 없음). 필요한 권한은 scripts/billing/README.md.

사용 예:
    python scripts/billing/gemini_cost_report.py                    # 이번 달 1일 ~ 오늘(KST)
    python scripts/billing/gemini_cost_report.py --days 30
    python scripts/billing/gemini_cost_report.py --rows tests/fixtures/billing/gemini_cost_rows.json   # 오프라인(샘플)

테이블 이름은 .env 의 REBOM_BILLING_TABLE (예: my-proj.billing_export.gcp_billing_export_v1_XXXXXX_XXXXXX_XXXXXX)
또는 --table 로 준다. 결과는 data/billing/ 아래(커밋 대상 아님)에 저장한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import webbrowser
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "billing"
KST = dt.timezone(dt.timedelta(hours=9), "KST")  # 한국은 서머타임이 없다. Windows 는 zoneinfo 에 tzdata 패키지가 필요해서 고정 오프셋을 쓴다
TABLE_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

# Gemini API 는 서비스 이름이 'Gemini API' 로, 예전 키는 'Generative Language API' 로 잡힌다.
SERVICE_FILTER = "(service.description LIKE '%Gemini%' OR service.description = 'Generative Language API')"


def build_query(table: str, start: dt.date, end: dt.date) -> str:
    """일자(KST)·SKU 별 사용량과 비용. end 는 포함."""
    if not TABLE_RE.match(table):
        raise ValueError(f"테이블 이름 형식이 이상합니다: {table!r}")
    # 파티션은 적재 시각 기준이라 하루 앞부터 읽고, 늦게 들어온 행은 이후 파티션에 있으니 끝은 열어 둔다.
    return f"""
SELECT
  FORMAT_DATE('%Y-%m-%d', DATE(usage_start_time, 'Asia/Seoul')) AS usage_date,
  project.id AS project_id,
  sku.description AS sku,
  SUM(usage.amount_in_pricing_units) AS usage_amount,
  ANY_VALUE(usage.pricing_unit) AS pricing_unit,
  SUM(cost) AS cost,
  SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS credits,
  ANY_VALUE(currency) AS currency
FROM `{table}`
WHERE DATE(_PARTITIONTIME) >= DATE_SUB(DATE '{start.isoformat()}', INTERVAL 1 DAY)
  AND DATE(usage_start_time, 'Asia/Seoul') BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'
  AND {SERVICE_FILTER}
GROUP BY usage_date, project_id, sku
ORDER BY usage_date, sku
"""


_MODEL_WORDS = {"pro", "flash", "lite", "image", "embedding", "preview", "live", "tts", "native", "audio", "nano", "ultra", "exp"}
_STOP_WORDS = {"input", "output", "text", "tokens", "token", "cached", "caching", "batch", "storage", "count", "generate", "content", "long", "short", "context", "thinking", "grounding", "search", "request", "requests"}


def model_of(sku: str) -> str:
    """SKU 설명에서 모델 이름만 뽑는다. 예: 'Gemini 3.1 Flash Lite Input Text Tokens' -> 'Gemini 3.1 Flash Lite'.

    형식을 모르는 SKU 는 설명 그대로 돌려준다(모델 칸에서 그대로 보이게).
    """
    words = sku.replace("-", " ").split()
    for i, w in enumerate(words):
        if w.lower() not in {"gemini", "gemma", "imagen", "veo"}:
            continue
        name = [w.capitalize()]
        for nxt in words[i + 1:]:
            low = nxt.lower().strip("(),")
            if low in _STOP_WORDS or not low:
                break
            if low in _MODEL_WORDS or re.fullmatch(r"\d+(\.\d+)?[a-z]?", low):
                name.append(nxt.strip("(),").capitalize() if not low[0].isdigit() else low)
            else:
                break
        if len(name) > 1:
            return " ".join(name)
    return sku


def kind_of(sku: str) -> str:
    low = sku.lower()
    if "cache" in low or "caching" in low:
        return "캐시"
    if "output" in low:
        return "출력"
    if "input" in low:
        return "입력"
    return "기타"


def summarize(rows: list[dict]) -> dict:
    """BigQuery 행 목록 -> 모델별 합계, 일자×모델 합계, SKU 상세."""
    models: dict[str, dict] = defaultdict(lambda: {"cost": 0.0, "net": 0.0, "by_kind": defaultdict(float)})
    daily: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    skus: dict[tuple, dict] = {}
    currencies = set()
    for r in rows:
        sku = r.get("sku") or "(SKU 없음)"
        cost = float(r.get("cost") or 0)
        net = cost + float(r.get("credits") or 0)
        model = model_of(sku)
        m = models[model]
        m["cost"] += cost
        m["net"] += net
        m["by_kind"][kind_of(sku)] += cost
        daily[r.get("usage_date") or "?"][model] += cost
        key = (model, sku)
        s = skus.setdefault(key, {"model": model, "sku": sku, "usage": 0.0, "unit": r.get("pricing_unit") or "", "cost": 0.0})
        s["usage"] += float(r.get("usage_amount") or 0)
        s["cost"] += cost
        if r.get("currency"):
            currencies.add(r["currency"])
    total = sum(m["cost"] for m in models.values())
    total_net = sum(m["net"] for m in models.values())
    ordered = sorted(models.items(), key=lambda kv: kv[1]["cost"], reverse=True)
    return {
        "currency": "/".join(sorted(currencies)) or "",
        "total": total,
        "total_net": total_net,
        "models": [{"model": k, "cost": v["cost"], "net": v["net"], "by_kind": dict(v["by_kind"]),
                    "share": (v["cost"] / total if total else 0.0)} for k, v in ordered],
        "daily": {d: dict(v) for d, v in sorted(daily.items())},
        "skus": sorted(skus.values(), key=lambda s: (-s["cost"], s["sku"])),
    }


def _money(x: float, cur: str) -> str:
    return f"{x:,.0f} {cur}".strip() if cur == "KRW" else f"{x:,.4f} {cur}".strip()


def render_html(summary: dict, start: dt.date, end: dt.date, table: str, generated: dt.datetime) -> str:
    cur = summary["currency"]
    e = html.escape
    palette = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626", "#0891b2", "#4b5563"]
    colors = {m["model"]: palette[i % len(palette)] for i, m in enumerate(summary["models"])}

    model_rows = "".join(
        f"<tr><td><span class='dot' style='background:{colors[m['model']]}'></span>{e(m['model'])}</td>"
        f"<td class='n'>{_money(m['cost'], cur)}</td><td class='n'>{m['share']*100:.1f}%</td>"
        f"<td class='n'>{_money(m['by_kind'].get('입력', 0), cur)}</td><td class='n'>{_money(m['by_kind'].get('출력', 0), cur)}</td>"
        f"<td class='n'>{_money(m['by_kind'].get('캐시', 0) + m['by_kind'].get('기타', 0), cur)}</td>"
        f"<td class='n'>{_money(m['net'], cur)}</td></tr>"
        for m in summary["models"]
    ) or "<tr><td colspan='7'>이 기간에 Gemini 사용 금액이 없습니다. (무료 등급 키 사용분은 0으로 잡힙니다)</td></tr>"

    day_max = max((sum(v.values()) for v in summary["daily"].values()), default=0) or 1
    bars = []
    for day, by_model in summary["daily"].items():
        segs = "".join(
            f"<span title='{e(model)}: {_money(c, cur)}' style='width:{c / day_max * 100:.2f}%;background:{colors.get(model, '#999')}'></span>"
            for model, c in sorted(by_model.items(), key=lambda kv: -kv[1]) if c > 0
        )
        bars.append(f"<div class='bar'><span class='d'>{e(day[5:])}</span><div class='track'>{segs}</div>"
                    f"<span class='n'>{_money(sum(by_model.values()), cur)}</span></div>")

    sku_rows = "".join(
        f"<tr><td>{e(s['model'])}</td><td>{e(s['sku'])}</td><td class='n'>{s['usage']:,.0f} {e(s['unit'])}</td>"
        f"<td class='n'>{_money(s['cost'], cur)}</td></tr>"
        for s in summary["skus"]
    )

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gemini 모델별 비용</title>
<style>
:root{{--bg:#fff;--fg:#111827;--mute:#6b7280;--line:#e5e7eb;--track:#f3f4f6}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111827;--fg:#f9fafb;--mute:#9ca3af;--line:#374151;--track:#1f2937}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,"Malgun Gothic",sans-serif}}
main{{max-width:960px;margin:0 auto;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 8px}}
.meta{{color:var(--mute);font-size:12px}}
.kpi{{display:flex;gap:24px;flex-wrap:wrap;margin:16px 0}} .kpi div{{min-width:160px}}
.kpi b{{display:block;font-size:24px}}
table{{width:100%;border-collapse:collapse}} th,td{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left}}
th{{color:var(--mute);font-weight:500;font-size:12px}} .n{{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}}
.dot{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}}
.bar{{display:grid;grid-template-columns:48px 1fr 120px;gap:8px;align-items:center;margin:3px 0}}
.track{{display:flex;height:14px;background:var(--track);border-radius:3px;overflow:hidden}}
.d{{color:var(--mute);font-size:12px}} .wrap{{overflow-x:auto}}
</style></head><body><main>
<h1>Gemini 모델별 비용</h1>
<div class="meta">기간 {start} ~ {end} (KST) · 출처 Cloud Billing BigQuery 내보내기 <code>{e(table)}</code> · 생성 {generated:%Y-%m-%d %H:%M} KST<br>
결제 데이터는 몇 시간 늦게 반영됩니다. 최근 하루 이틀 값은 이후 늘어날 수 있습니다.</div>
<div class="kpi"><div><span class="meta">청구 금액(크레딧 전)</span><b>{_money(summary['total'], cur)}</b></div>
<div><span class="meta">크레딧 반영 후</span><b>{_money(summary['total_net'], cur)}</b></div>
<div><span class="meta">모델 수</span><b>{len(summary['models'])}</b></div></div>
<h2>모델별</h2><div class="wrap"><table><thead><tr><th>모델</th><th class="n">금액</th><th class="n">비중</th><th class="n">입력</th><th class="n">출력</th><th class="n">캐시·기타</th><th class="n">크레딧 후</th></tr></thead>
<tbody>{model_rows}</tbody></table></div>
<h2>일별</h2>{''.join(bars) or '<p class="meta">데이터 없음</p>'}
<h2>SKU 상세</h2><div class="wrap"><table><thead><tr><th>모델</th><th>SKU</th><th class="n">사용량</th><th class="n">금액</th></tr></thead>
<tbody>{sku_rows}</tbody></table></div>
</main></body></html>
"""


def fetch_rows(table: str, start: dt.date, end: dt.date, billing_project: str | None) -> list[dict]:
    try:
        from google.cloud import bigquery
    except ImportError:
        sys.exit("google-cloud-bigquery 가 필요합니다: pip install google-cloud-bigquery")
    client = bigquery.Client(project=billing_project or table.split(".")[0])
    return [dict(r.items()) for r in client.query(build_query(table, start, end)).result()]


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")  # 읽기만 한다


def main(argv: list[str] | None = None) -> int:
    _load_env()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--table", default=os.getenv("REBOM_BILLING_TABLE"), help="project.dataset.gcp_billing_export_v1_XXXX")
    p.add_argument("--start", type=dt.date.fromisoformat, help="YYYY-MM-DD (기본: 이번 달 1일)")
    p.add_argument("--end", type=dt.date.fromisoformat, help="YYYY-MM-DD, 포함 (기본: 오늘)")
    p.add_argument("--days", type=int, help="오늘부터 N일 전까지 (--start 대신)")
    p.add_argument("--project", default=os.getenv("REBOM_BILLING_QUERY_PROJECT"), help="쿼리 비용을 낼 프로젝트 (기본: 테이블 프로젝트)")
    p.add_argument("--rows", type=Path, help="BigQuery 대신 읽을 행 JSON (테스트·오프라인용)")
    p.add_argument("--out", type=Path, help="저장할 HTML 경로 (기본: data/billing/gemini_cost_<기간>.html)")
    p.add_argument("--json", action="store_true", help="HTML 옆에 요약 JSON 도 저장")
    p.add_argument("--open", action="store_true", help="만든 HTML 을 브라우저로 연다")
    a = p.parse_args(argv)

    today = dt.datetime.now(KST).date()
    end = a.end or today
    start = a.start or (end - dt.timedelta(days=a.days - 1) if a.days else end.replace(day=1))
    if start > end:
        p.error("--start 가 --end 보다 늦습니다")

    if a.rows:
        rows = json.loads(a.rows.read_text(encoding="utf-8"))
        table = f"(파일) {a.rows.name}"
    else:
        if not a.table:
            p.error("--table 또는 .env 의 REBOM_BILLING_TABLE 이 필요합니다")
        rows = fetch_rows(a.table, start, end, a.project)
        table = a.table

    summary = summarize(rows)
    out = a.out or OUT_DIR / f"gemini_cost_{start:%Y%m%d}_{end:%Y%m%d}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(summary, start, end, table, dt.datetime.now(KST)), encoding="utf-8", newline="\n")
    if a.json:
        out.with_suffix(".json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    cur = summary["currency"]
    print(f"{start} ~ {end}  합계 {_money(summary['total'], cur)} (크레딧 후 {_money(summary['total_net'], cur)})")
    for m in summary["models"]:
        print(f"  {m['model']:<32} {_money(m['cost'], cur):>16}  {m['share']*100:5.1f}%")
    print(f"저장: {out}")
    if a.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
