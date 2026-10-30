"""구조동물 사진 URL 이 연도별로 얼마나 열리는지 표본 조사 (열림 비율, 평균 용량).

연도마다 12개월에서 달마다 무작위 페이지를 뽑아 약 100건을 샘플링하고, popfile1 을 내려받아 용량만 잰다(저장하지 않음).
결과: data/sample/aggregates/photo_check_by_year.csv
"""
import csv
import datetime as dt
import random
import statistics
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rescue_api import RESCUE_URL, call, real_items  # noqa: E402

OUT = ROOT / "data" / "sample" / "aggregates" / "photo_check_by_year.csv"
YEARS = range(2019, 2027)
PER_MONTH = 9
random.seed(7)


def sample_month(year: int, month: int) -> list[dict]:
    s = dt.date(year, month, 1)
    e = (s.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    if s > dt.date.today():
        return []
    e = min(e, dt.date.today())
    q = {"bgnde": s.strftime("%Y%m%d"), "endde": e.strftime("%Y%m%d")}
    try:
        total = call({"numOfRows": 1, "pageNo": 1, **q}, url=RESCUE_URL, timeout=30).total_count or 0
        if not total:
            return []
        page = random.randint(1, max(1, total // PER_MONTH))
        return real_items(call({"numOfRows": PER_MONTH, "pageNo": page, **q}, url=RESCUE_URL, timeout=30).items)
    except requests.RequestException as exc:  # 메시지에 키가 담기므로 종류만 남긴다
        print(f"  {year}-{month:02d} 건너뜀: {type(exc).__name__}")
        return []


def fetch_size(url: str) -> tuple[int, int]:
    """(HTTP 상태, 받은 바이트). 연결 실패는 상태 0."""
    try:
        r = requests.get(url, timeout=20)
        return r.status_code, len(r.content)
    except requests.RequestException:
        return 0, 0


if __name__ == "__main__":
    rows = []
    for y in YEARS:
        items = [i for m in range(1, 13) for i in sample_month(y, m)]
        res = [fetch_size(i["popfile1"]) for i in items if i.get("popfile1")]
        ok = [b for st, b in res if st == 200 and b > 0]
        n = len(res)
        rows.append((y, len(items), n, len(ok), round(len(ok) / n * 100, 1) if n else 0,
                     round(statistics.mean(ok) / 1024) if ok else 0))
        print(f"{y}: 표본 {n}건, 열림 {len(ok)} ({rows[-1][4]}%), 평균 {rows[-1][5]}KB")
        time.sleep(0.2)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["year", "sampled", "checked", "opened", "open_pct", "avg_kb"])
        w.writerows(rows)
    print(f"저장: {OUT.relative_to(ROOT)}")
