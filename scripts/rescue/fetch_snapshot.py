"""읽기 전용 호출로 증분 시험에 쓸 네 가지 조회 결과를 한 파일에 저장한다.

updated      : 수정일 증분 (bgupd=--since ~ 오늘)
list_notice  : 보호중 목록, 공고 기간 안 (state=notice, 발생일 --from ~ 오늘)
list_protect : 보호중 목록, 공고 기간 지남 (state=protect)
full         : 정답. 발생일 --from ~ 오늘을 7일 단위로 전부 다시 받은 결과
사용: python fetch_snapshot.py OUT.json --since 20261028 --from 20260701   (호출 약 50번)
"""
import argparse
import datetime as dt
import json
import time

import requests

from rescue_api import call, real_items


def fetch(params: dict) -> dict[str, dict]:
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
    snap["updated"] = fetch({"bgupd": a.since, "enupd": td})
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
