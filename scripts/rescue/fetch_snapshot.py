"""읽기 전용 호출로 증분 시험에 쓸 네 가지 조회 결과를 한 파일에 저장한다.

updated      : 수정일 증분 (bgupd=--since ~ 오늘, 발생일 범위는 오늘 기준 3년 전부터)
list_notice  : 보호중 목록, 공고 기간 안 (state=notice, 발생일 --from ~ 오늘)
list_protect : 보호중 목록, 공고 기간 지남 (state=protect)
full         : 정답. 발생일 --from ~ 오늘을 7일 단위로 전부 다시 받은 결과
사용: python fetch_snapshot.py OUT.json --since 20261028 --from 20260701   (호출 약 50번)

주의: 구조 API는 발생일 범위(bgnde/endde)를 안 보내면 수정일 조회(bgupd/enupd)도 발생일 최근 약 1개월분만
돌려주고, 발생일 범위가 약 3년(1,096일)을 넘으면 오류 없이 0건을 돌려준다. 그래서 수정일 조회에는 항상
발생일 범위를 붙이고, 범위가 3년을 넘지 않게 확인한다.
"""
import argparse
import datetime as dt
import json
import time

import requests

from rescue_api import call, real_items

MAX_SPAN_DAYS = 1095  # 1,096일까지 동작하는 것을 확인했고, 여유를 둔다


def check_span(params: dict) -> None:
    """발생일 범위가 3년을 넘으면 API가 오류 없이 0건을 돌려주므로 미리 막는다."""
    if "bgnde" in params and "endde" in params:
        b = dt.datetime.strptime(params["bgnde"], "%Y%m%d").date()
        e = dt.datetime.strptime(params["endde"], "%Y%m%d").date()
        if (e - b).days > MAX_SPAN_DAYS:
            raise SystemExit(f"발생일 범위가 {MAX_SPAN_DAYS}일을 넘습니다. 나눠서 요청하세요: {params}")


def fetch(params: dict) -> dict[str, dict]:
    check_span(params)
    out = {}
    for page in range(1, 80):
        for attempt in range(4):
            try:
                r = call({"numOfRows": 1000, "pageNo": page, **params})
                if r.ok:
                    break
            except requests.RequestException as exc:  # 메시지에 키가 담기므로 종류만 남긴다
                print("  재시도:", type(exc).__name__)
            time.sleep(3 * (attempt + 1))
        else:
            raise SystemExit(f"실패 {params} p{page}")
        if page == 1 and r.total_count == 0:
            print("  경고: 0건입니다. 정말 없는지, 범위가 너무 넓은지 확인하세요:", params)
        for i in real_items(r.items):
            out[i["desertionNo"]] = i
        if len(r.items) < 1000:
            return out
        time.sleep(0.15)
    raise SystemExit(f"페이지 80개 초과 {params}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--since", required=True, help="수정일 시작 YYYYMMDD (이전 수집 저장일)")
    ap.add_argument("--from", dest="start", required=True, help="발생일 시작 YYYYMMDD")
    a = ap.parse_args()

    today = dt.date.today()
    td = today.strftime("%Y%m%d")
    snap = {"fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    # 수정일 조회에는 발생일 범위가 꼭 필요하다(안 보내면 최근 1개월분만 나옴)
    span_start = (today - dt.timedelta(days=MAX_SPAN_DAYS)).strftime("%Y%m%d")
    snap["updated"] = fetch({"bgupd": a.since, "enupd": td, "bgnde": span_start, "endde": td})
    snap["list_notice"] = fetch({"state": "notice", "bgnde": a.start, "endde": td})
    snap["list_protect"] = fetch({"state": "protect", "bgnde": a.start, "endde": td})
    full = {}
    s = dt.datetime.strptime(a.start, "%Y%m%d").date()
    while s <= today:
        e = min(s + dt.timedelta(days=6), today)
        full.update(fetch({"bgnde": s.strftime("%Y%m%d"), "endde": e.strftime("%Y%m%d")}))
        s = e + dt.timedelta(days=1)
    snap["full"] = full
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    print({k: len(v) for k, v in snap.items() if k != "fetched_at"})
