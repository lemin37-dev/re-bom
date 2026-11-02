"""구조동물 API 로 조회 가능한 전체 기간의 월별 건수 (totalCount) 를 모아 data/sample/aggregates/rescue_counts.csv 에 저장.

numOfRows=1 로 호출해 건수만 읽는다. 레코드는 저장하지 않는다.
"""
import datetime as dt
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rescue_api import RESCUE_URL, call  # noqa: E402

OUT = ROOT / "data" / "sample" / "aggregates" / "rescue_counts.csv"
START = dt.date(2008, 1, 1)


def count(s: dt.date, e: dt.date) -> int:
    why = ""
    for attempt in range(5):
        try:
            r = call({"numOfRows": 1, "pageNo": 1, "bgnde": s.strftime("%Y%m%d"), "endde": e.strftime("%Y%m%d")}, url=RESCUE_URL, timeout=30)
            if r.ok and r.total_count is not None:
                return r.total_count
            why = f"{r.result_code} {r.result_msg}"
        except requests.RequestException as exc:  # 연결 시간 초과 등. 메시지에 키가 담기므로 예외 종류만 남긴다
            why = type(exc).__name__
        time.sleep(5 * (attempt + 1))
    raise SystemExit(f"실패 {s}~{e}: {why}")


def load_done() -> dict[str, int]:
    if not OUT.exists():
        return {}
    lines = OUT.read_text(encoding="utf-8").splitlines()[1:]
    return {m: int(n) for m, n in (l.split(",") for l in lines)}


def save(rows: dict[str, int]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("month,count\n" + "".join(f"{m},{n}\n" for m, n in sorted(rows.items())), encoding="utf-8")


def month_end(d: dt.date) -> dt.date:
    nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return nxt - dt.timedelta(days=1)


if __name__ == "__main__":
    # 끊겨도 다시 실행하면 이어서 센다. 이번 달은 늘어나므로 항상 다시 센다
    today = dt.date.today()
    done, s = load_done(), START
    done.pop(today.strftime("%Y-%m"), None)
    while s <= today:
        e = min(month_end(s), today)
        m = s.strftime("%Y-%m")
        if m not in done:
            done[m] = count(s, e)
            save(done)
            time.sleep(0.1)
        s = e + dt.timedelta(days=1)

    rows = sorted(done.items())

    by_year = {}
    for m, n in rows:
        by_year[m[:4]] = by_year.get(m[:4], 0) + n
    for y, n in by_year.items():
        print(f"{y}: {n:,}")
    print(f"전체: {sum(n for _, n in rows):,}건 ({rows[0][0]} ~ {rows[-1][0]}, 호출 {len(rows)}회)")
    jul_oct = sum(n for m, n in rows if "2026-07" <= m <= "2026-10")
    print(f"2026-07~10 월별 합: {jul_oct:,}")
    print(f"저장: {OUT.relative_to(ROOT)}")
