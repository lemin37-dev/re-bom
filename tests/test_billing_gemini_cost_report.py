"""scripts/billing/gemini_cost_report.py: SKU 파싱·집계·HTML (BigQuery 호출 없이 샘플 행 사용)."""
import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "billing" / "gemini_cost_rows.json"

_spec = importlib.util.spec_from_file_location("gemini_cost_report", ROOT / "scripts" / "billing" / "gemini_cost_report.py")
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)


@pytest.mark.parametrize("sku, model, kind", [
    ("Gemini 3.1 Flash Lite Input Text Tokens", "Gemini 3.1 Flash Lite", "입력"),
    ("Gemini 3.5 Flash Output Text Tokens", "Gemini 3.5 Flash", "출력"),
    ("Gemini 3.5 Flash Cached Input Tokens", "Gemini 3.5 Flash", "캐시"),
    ("Gemini Embedding 2 Input Text Tokens", "Gemini Embedding 2", "입력"),
    ("Generate content input token count gemini-2.5-pro", "Gemini 2.5 Pro", "입력"),
    ("Some Unknown SKU", "Some Unknown SKU", "기타"),
])
def test_model_and_kind(sku, model, kind):
    assert report.model_of(sku) == model
    assert report.kind_of(sku) == kind


def test_summarize_fixture():
    s = report.summarize(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert s["currency"] == "KRW"
    assert s["total"] == pytest.approx(1032.0)
    assert s["total_net"] == pytest.approx(982.0)
    top = s["models"][0]
    assert top["model"] == "Gemini 3.1 Flash Lite"
    assert top["cost"] == pytest.approx(760.0)
    assert top["by_kind"] == {"입력": 600.0, "출력": 160.0}
    assert top["net"] == pytest.approx(710.0)
    assert sum(m["share"] for m in s["models"]) == pytest.approx(1.0)
    assert list(s["daily"]) == ["2026-11-15", "2026-11-16"]
    assert s["daily"]["2026-11-15"]["Gemini 3.1 Flash Lite"] == pytest.approx(510.0)


def test_summarize_missing_keys():
    s = report.summarize([{"sku": "Gemini 3.1 Flash Lite Input Text Tokens"}])  # 빈 필드는 키가 빠져 온다
    assert s["total"] == 0 and s["models"][0]["model"] == "Gemini 3.1 Flash Lite"


def test_build_query_rejects_bad_table():
    q = report.build_query("p.ds.gcp_billing_export_v1_X", dt.date(2026, 11, 1), dt.date(2026, 11, 17))
    assert "DATE '2026-11-01' AND DATE '2026-11-17'" in q and "`p.ds.gcp_billing_export_v1_X`" in q
    with pytest.raises(ValueError):
        report.build_query("p.ds.t`; DROP", dt.date(2026, 11, 1), dt.date(2026, 11, 17))


def test_cli_offline(tmp_path, capsys):
    out = tmp_path / "r.html"
    assert report.main(["--rows", str(FIXTURE), "--start", "2026-11-15", "--end", "2026-11-16", "--out", str(out), "--json"]) == 0
    page = out.read_text(encoding="utf-8")
    assert "Gemini 3.1 Flash Lite" in page and "1,032 KRW" in page
    assert json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))["total"] == pytest.approx(1032.0)
    assert "합계 1,032 KRW" in capsys.readouterr().out


def test_empty_rows_render():
    page = report.render_html(report.summarize([]), dt.date(2026, 11, 1), dt.date(2026, 11, 2), "t", dt.datetime(2026, 11, 17))
    assert "사용 금액이 없습니다" in page
