"""구조동물 공고 증분 수집. docs/data_contract.md 의 2·3·5·8절을 구현한다.

매일 한 번 실행한다.
  ① 수정일 증분   bgupd=마지막 성공일, enupd=오늘 (+ 발생일 범위를 3년 이하로 나눠서)
  ② 보호중 목록   state=notice, state=protect 전부 (발생일 범위를 나눠서)
  ③ 사라진 공고   이전 실행의 보호중 목록에는 있고 이번에는 없는 공고. 구간별로 판정한다(계약 3절 종료 판정 조건).
                  - 그 공고의 발생일을 덮는 notice·protect 구간이 모두 완전하면(결과 코드 00, 1,095일 이하, 받은 건수 = totalCount)
                    "종료(사유 미상)" 이벤트로 확정한다(confirmed_by=complete_range).
                  - 덮는 구간 중 하나라도 불완전하면 desertion_no + 발생일로 한 건씩 조회한다.
                    종료로 확인되면 이벤트(confirmed_by=lookup), 아직 보호중이면 유지, 조회 실패·0건·불확실이면
                    기존 상태를 유지하고 partitions 의 reprocess_ids 에 남겨 다음 실행에서 다시 본다.
                  다시 나타난 공고 -> "보호중 복귀" 이벤트. 종료 사유는 받지 않는다.
                  --lookup 을 주면 확정된 공고의 발생일만 조건 없이 조회해 최종 값을 받는다(필요할 때만).

결과 (bronze/rescue/raw/dt=<실행일>/run_id=<id>/):
  updated / list_notice / list_protect / lookup / events .jsonl.gz, manifest.json (partitions: 구간별 총건수·받은 건수·완전 여부)
상태 (state/rescue_state.json): 마지막 성공 enupd, 보호중 공고 목록(번호 -> 발생일), 종료로 표시한 공고
저장 위치: 환경변수 REBOM_S3_BUCKET 이 있으면 S3, 없으면 로컬 data/ (rebom.ingestion.storage).
올리는 순서: 종류별 파일 -> events -> manifest.json(마지막, 있으면 그 실행이 끝까지 올라간 것) -> 상태 파일.
  업로드가 하나라도 실패하면 상태를 갱신하지 않고 끝낸다. 그래야 다음 실행이 그 구간을 건너뛰지 않는다.

사용 예 (레포 루트에서):
  PYTHONPATH=src python -m rebom.ingestion.rescue_incremental --dry-run         # 호출만 하고 아무것도 쓰지 않음
  PYTHONPATH=src python -m rebom.ingestion.rescue_incremental --since 20261101  # 첫 실행(상태 없음)의 수정일 시작일
  PYTHONPATH=src python -m rebom.ingestion.rescue_incremental --lookup          # 확정된 사라진 공고의 최종 값도 조회

인증키는 .env 의 DATA_GO_KR_SERVICE_KEY. 출력·로그·파일에 남기지 않고, 오류는 예외 종류만 기록한다.
보호자 실명(careOwnerNm)은 저장하지 않는다(AGENTS.md).
"""

from __future__ import annotations

import argparse
import gzip
from collections import Counter
import hashlib
import json
import sys
import tempfile
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from rebom.ingestion.rescue_api import RESCUE_URL as URL
from rebom.ingestion.rescue_api import call, explain_error, real_items
from rebom.ingestion.storage import UploadError, make_store

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data"  # 로컬 저장일 때 키가 이 아래에 그대로 놓인다
RAW_PREFIX = "bronze/rescue/raw"
STATE_KEY = "state/rescue_state.json"
KST = timezone(timedelta(hours=9))

HAPPEN_START = date(2008, 1, 1)  # API 에 있는 가장 오래된 공고
MAX_SPAN_DAYS = 1000  # 발생일 범위가 1,096일을 넘으면 오류 없이 0건이 나온다 (11/2 확인)
PAGE_SIZE = 1000
MAX_PARTITION_DAYS = 1095  # B 의 validate_partition 한도(양 끝 포함). 계약 3절 종료 판정 조건 ②
MAX_VANISH_RATIO = 0.2  # 계약에는 없는 추가 안전장치: 보호중 목록에서 한 번에 이만큼 넘게 사라지면 API 이상으로 보고 중단
MAX_LOOKUP_IDS = 300  # 불완전한 구간의 사라진 공고를 한 건씩 조회하는 상한. 넘는 공고는 재처리로 남긴다(구간을 날 단위로 좁히기 때문에 평소에는 거의 없다)
MAX_CALLS = 600  # 한 번에 쓸 호출 수 안전장치. 일 한도 10,000건의 약 6%
DROP_FIELDS = ("careOwnerNm",)  # 보호자 실명은 저장하지 않는다(AGENTS.md). 데이터 계약 9절 방식 A
CODE_VERSION = "2026-11-02"


def log(msg: str) -> None:
    print(msg, flush=True)


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def parse_ymd(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def dash(s: str) -> str:
    """YYYYMMDD -> YYYY-MM-DD. B 의 validate_partition 과 manifest partitions 는 하이픈 형식을 쓴다."""
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def partition_status(result_code, start_date: str, end_date: str, total_count, received_count) -> tuple[bool, str]:
    """B 의 validate_partition(src/rebom/storage/silver/validate_manifest.py, PR #6)과 같은 조건·같은 사유.
    결과 코드 00, 기간 1~1,095일(양 끝 포함), 건수는 0 이상 정수, 받은 건수 = totalCount."""
    if str(result_code) != "00":
        return False, "api_result_error"
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False, "invalid_date"
    if not 1 <= (end - start).days + 1 <= MAX_PARTITION_DAYS:
        return False, "invalid_date_range"
    if type(total_count) is not int or type(received_count) is not int or total_count < 0 or received_count < 0:
        return False, "invalid_count"
    if total_count != received_count:
        return False, "count_mismatch"
    return True, "complete"


class ApiFailure(RuntimeError):
    """한 호출이 재시도 후에도 실패했다. 일일 한도 초과·호출 상한은 SystemExit 로 따로 끝낸다."""


def happen_ranges(start: date, end: date) -> list[tuple[str, str]]:
    """발생일 범위를 MAX_SPAN_DAYS 이하 구간으로 나눈다."""
    out, s = [], start
    while s <= end:
        e = min(s + timedelta(days=MAX_SPAN_DAYS - 1), end)
        assert (e - s).days + 1 <= MAX_SPAN_DAYS
        out.append((ymd(s), ymd(e)))
        s = e + timedelta(days=1)
    return out


class Fetcher:
    """페이지를 넘기며 받고, 호출 수·경고·버린 항목 수를 센다."""

    def __init__(self, max_calls: int):
        self.max_calls = max_calls
        self.calls = 0
        self.warnings: list[str] = []

    def _get(self, params: dict):
        for attempt in range(4):
            if self.calls >= self.max_calls:
                raise SystemExit(f"호출 {self.max_calls}번 초과로 중단")
            try:
                self.calls += 1
                r = call(params, url=URL, timeout=60)
            except requests.RequestException as e:  # 메시지에 URL(키)이 들어갈 수 있어 종류만 남긴다
                log(f"  요청 실패({type(e).__name__}), 재시도 {attempt + 1}/4")
                time.sleep(3 * (attempt + 1))
                continue
            if r.ok:
                return r
            if r.result_code == "22":
                raise SystemExit("일일 호출 한도 초과(코드 22)")
            log(f"  응답 오류: {explain_error(r)}, 재시도 {attempt + 1}/4")
            time.sleep(3 * (attempt + 1))
        raise ApiFailure(f"4번 재시도 후에도 실패: {params.get('state') or 'updated'} {params.get('bgnde')}")

    def fetch(self, kind: str, base: dict, ranges: list[tuple[str, str]]) -> tuple[list[dict], dict]:
        """ranges 마다 끝까지 받아 (항목 목록, 요약) 을 돌려준다. 같은 공고번호는 마지막 것만 둔다.
        요약의 partitions 는 구간마다 한 줄이다. lookup 종류는 partitions 에 넣지 않는다."""
        got: dict[str, dict] = {}
        summary = {"request_params": [], "partitions": [], "page_count": 0, "dropped_count": 0, "duplicate_count": 0}
        for bgnde, endde in ranges:
            params = {**base, "bgnde": bgnde, "endde": endde, "numOfRows": PAGE_SIZE}
            range_ids: set[str] = set()
            by_day: Counter = Counter()  # 구간 안에서 받은 서로 다른 공고의 발생일별 건수 (불완전한 구간을 쪼갤 때 쓴다)
            dropped, duplicates, total, code = 0, 0, None, None
            for page in range(1, 200):
                r = self._get({**params, "pageNo": page})
                summary["page_count"] += 1
                code = str(r.result_code)
                total = r.total_count if total is None else total
                for item in r.items:
                    if not item or not item.get("desertionNo"):  # 빈 객체·번호 없는 항목은 버리지 않고 센다
                        dropped += 1
                        continue
                    if item["desertionNo"] in range_ids:
                        duplicates += 1
                    else:
                        by_day[str(item.get("happenDt", ""))] += 1
                    range_ids.add(item["desertionNo"])
                    got[item["desertionNo"]] = item
                if len(r.items) < PAGE_SIZE or (total is not None and page * PAGE_SIZE >= total):
                    break
                time.sleep(0.15)
            summary["dropped_count"] += dropped
            summary["duplicate_count"] += duplicates
            summary["request_params"].append({**params, "pageNo": "1..n"})
            if kind.startswith("lookup"):
                continue
            # 받은 건수는 구간 안의 서로 다른 공고번호 수다. 페이지가 밀려 중복이 생기면 빠진 건을 가리지 못하게 중복은 뺀다.
            complete, reason = partition_status(code, dash(bgnde), dash(endde), total, len(range_ids))
            if not complete and reason == "count_mismatch" and kind in ("list_notice", "list_protect"):
                # 총건수보다 적게 받은 구간은 반으로 쪼개 어긋나는 날만 좁힌다. 쪼개진 구간들이 원래 구간을 대신한다(겹치지 않게).
                leaves = self._narrow(kind, base, parse_ymd(bgnde), parse_ymd(endde), by_day, code, total, summary)
                leaves[0]["dropped_count"], leaves[0]["duplicate_count"] = dropped, duplicates
                parts = leaves
            else:
                parts = [self._part(kind, code, bgnde, endde, total, len(range_ids), complete, reason, dropped, duplicates)]
            summary["partitions"].extend(parts)
            for p in parts:
                if not p["complete"]:
                    self.warnings.append(
                        f"{kind} {p['start_date']}~{p['end_date']}: 불완전({p['reason']}) 총건수 {p['total_count']}, 받은 건수 {p['received_count']}"
                    )
            if kind in ("list_notice", "list_protect") and total == 0 and endde >= ymd(date.today() - timedelta(days=60)):
                self.warnings.append(f"{kind} {dash(bgnde)}~{dash(endde)}: 최근 구간인데 0건(참고)")
        return list(got.values()), summary

    @staticmethod
    def _part(kind, code, bgnde, endde, total, received, complete, reason, dropped=0, duplicates=0) -> dict:
        return {
            "kind": kind, "result_code": code, "start_date": dash(bgnde), "end_date": dash(endde),
            "total_count": total, "received_count": received, "dropped_count": dropped, "duplicate_count": duplicates,
            "complete": complete, "reason": reason, "reprocess_ids": [],
        }

    def _count(self, kind: str, base: dict, s: date, e: date, summary: dict):
        """구간의 totalCount 만 본다(numOfRows=1). 11/2 확인: 구조 API 의 totalCount 는 월별 합계와 연 단위 값이 일치한다."""
        params = {**base, "bgnde": ymd(s), "endde": ymd(e), "numOfRows": 1, "pageNo": 1}
        r = self._get(params)
        summary["page_count"] += 1
        summary["request_params"].append(params)
        return r.total_count

    def _narrow(self, kind: str, base: dict, s: date, e: date, by_day: Counter, code: str, total, summary: dict) -> list[dict]:
        """총건수와 받은 건수가 다른 구간 [s, e] 를 반으로 쪼개 어긋나는 하위 구간까지 내려간다. 하루까지 내려가도 다르면 불완전로 남긴다.
        이미 받은 항목의 발생일별 건수(by_day)로 받은 건수를 구하므로 하위 구간마다 totalCount 한 번만 더 부른다."""
        a, b = ymd(s), ymd(e)
        received = sum(n for day, n in by_day.items() if a <= day <= b)
        complete, reason = partition_status(code, dash(a), dash(b), total, received)
        if complete or s == e or reason != "count_mismatch":
            return [self._part(kind, code, a, b, total, received, complete, reason)]
        mid = s + (e - s) // 2
        out: list[dict] = []
        for lo, hi in ((s, mid), (mid + timedelta(days=1), e)):
            out += self._narrow(kind, base, lo, hi, by_day, code, self._count(kind, base, lo, hi, summary), summary)
        return out

    def lookup_id(self, desertion_no: str, happen: str | None) -> tuple[list[dict] | None, dict | None]:
        """불완전한 구간의 사라진 공고를 desertion_no 와 발생일로 한 건 조회한다. 실패하면 (None, None).
        발생일 범위를 빼면 최근 1개월 밖의 공고는 오류 없이 0건이 나오므로 반드시 보낸다(11/2 확인)."""
        if not happen:
            return None, None
        try:
            items, summary = self.fetch("lookup_id", {"desertion_no": desertion_no}, [(happen, happen)])
        except ApiFailure:
            return None, None
        return [i for i in items if i["desertionNo"] == desertion_no], summary


def diff_states(prev_protected: dict, ended: dict, current_ids: set) -> tuple[list[str], list[str]]:
    """(사라진 공고, 복귀한 공고). 사라짐 = 이전 보호중이었는데 이번 목록에 없음, 복귀 = 종료로 표시했는데 목록에 다시 나타남."""
    vanished = sorted(set(prev_protected) - current_ids)
    returned = sorted(set(ended) & current_ids)
    return vanished, returned


def covering_partition(partitions: list[dict], kind: str, happen: str) -> dict | None:
    """발생일(YYYYMMDD)을 덮는 구간. 구간들은 겹치지 않는다."""
    d = dash(happen)
    return next((p for p in partitions if p["kind"] == kind and p["start_date"] <= d <= p["end_date"]), None)


def split_vanished(vanished: list[str], prev_protected: dict, partitions: list[dict]) -> tuple[list[str], list[str]]:
    """(구간 판정으로 확정, 한 건씩 확인 필요). 발생일을 덮는 notice·protect 구간이 모두 완전해야 확정한다.
    notice 에서 protect 로 옮겨 갔을 수도 있어 둘 다 본다. 발생일을 모르면 확인 필요로 둔다."""
    sure, check = [], []
    for v in vanished:
        happen = prev_protected.get(v)
        cover = [covering_partition(partitions, k, happen) for k in ("list_notice", "list_protect")] if happen else []
        (sure if cover and all(p and p["complete"] for p in cover) else check).append(v)
    return sure, check


def write_jsonl_gz(path: Path, rows: list[dict]) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"file": path.name, "record_count": len(rows), "checksum": hashlib.sha256(path.read_bytes()).hexdigest()}  # 계약 5절 manifest 필드 이름


def drop_fields(rows: list[dict], fields: tuple) -> list[dict]:
    return [{k: v for k, v in r.items() if k not in fields} for r in rows]


def load_state(store) -> dict:
    data = store.get_bytes(STATE_KEY)
    return json.loads(data.decode("utf-8")) if data else {}


def save_state(store, state: dict) -> None:
    store.put_bytes(STATE_KEY, json.dumps(state, ensure_ascii=False).encode("utf-8"), overwrite=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="수정일 조회 시작일 YYYYMMDD (상태가 없는 첫 실행에서만 쓰임, 기본: 어제)")
    ap.add_argument("--lookup", action="store_true", help="확정된 사라진 공고의 발생일을 조건 없이 조회해 최종 값을 받는다")
    ap.add_argument("--max-lookups", type=int, default=MAX_LOOKUP_IDS, help="불완전한 구간의 사라진 공고를 한 건씩 조회하는 상한")
    ap.add_argument("--dry-run", action="store_true", help="API 만 호출하고 파일·상태를 쓰지 않는다")
    ap.add_argument("--max-calls", type=int, default=MAX_CALLS, help="한 번에 쓸 최대 호출 수(일 한도 10,000건의 안전장치)")
    args = ap.parse_args()

    now = datetime.now(KST)
    today = now.date()
    store = make_store(DATA_DIR)
    state = load_state(store)
    first_run = not state
    if first_run:
        bgupd = parse_ymd(args.since) if args.since else today - timedelta(days=1)
    else:
        # 03:00 실행이면 마지막 성공일 오후에 바뀐 공고가 다음 실행에서 빠진다. 마지막 성공일을 하루 겹쳐 받고 B 가 MERGE 로 거른다.
        bgupd = parse_ymd(state["last_enupd"])
    run_id = f"{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    log(f"실행 {run_id} · 수정일 {ymd(bgupd)}~{ymd(today)} · {'첫 실행(기준선만)' if first_run else '증분'}")

    fx = Fetcher(args.max_calls)
    ranges = happen_ranges(HAPPEN_START, today)
    kinds: dict[str, tuple[list[dict], dict]] = {}
    kinds["updated"] = fx.fetch("updated", {"bgupd": ymd(bgupd), "enupd": ymd(today)}, ranges)
    kinds["list_notice"] = fx.fetch("list_notice", {"state": "notice"}, ranges)
    kinds["list_protect"] = fx.fetch("list_protect", {"state": "protect"}, ranges)
    partitions = [p for k in ("updated", "list_notice", "list_protect") for p in kinds[k][1]["partitions"]]
    for k, (rows, s) in kinds.items():
        bad = sum(not p["complete"] for p in s["partitions"])
        log(f"  {k}: {len(rows):,}건 · 페이지 {s['page_count']} · 버린 항목 {s['dropped_count']} · 불완전 구간 {bad}/{len(s['partitions'])}")

    protected = {i["desertionNo"]: i.get("happenDt", "") for k in ("list_notice", "list_protect") for i in kinds[k][0]}
    prev = state.get("protected", {})
    ended = state.get("ended", {})
    vanished, returned = ([], []) if first_run else diff_states(prev, ended, set(protected))
    log(f"  보호중 {len(protected):,}건 (이전 {len(prev):,}) · 사라짐 {len(vanished)} · 복귀 {len(returned)}")

    # 보호중 목록이 비었거나 한꺼번에 많이 사라지면 API 이상으로 보고 상태를 갱신하지 않는다.
    aborted = None
    if not protected:
        aborted = "보호중 목록이 0건"
    elif prev and len(vanished) > len(prev) * MAX_VANISH_RATIO:
        aborted = f"보호중 {len(vanished)}/{len(prev)}건이 한꺼번에 사라짐"
    if aborted:
        fx.warnings.append(f"중단: {aborted}. 상태를 갱신하지 않음")

    # 사라진 공고 확정. 구간이 완전하면 바로, 불완전하면 한 건씩 조회해서 확인한다. 확인 못 하면 기존 상태를 유지하고 재처리로 남긴다.
    confirmed: dict[str, str] = {}  # 공고번호 -> complete_range | lookup
    still_protected: dict[str, str] = {}  # 목록에서 빠졌지만 개별 조회로 보호중임을 확인한 공고 (번호 -> 발생일)
    reprocess: list[str] = []
    lookup_rows: list[dict] = []
    lookup_params, lookup_pages, lookup_dropped = [], 0, 0
    updated_by_id = {i["desertionNo"]: i for i in kinds["updated"][0]}
    relooked: dict[str, dict | None] = {}  # 구간으로 확정했지만 수정분 상태가 종료가 아니어서 다시 조회한 공고 -> 조회한 종료 행(없으면 None)
    lookup_final: dict[str, dict] = {}  # 불완전 구간이라 번호로 조회해 종료를 확인한 공고 -> 조회한 행
    conflicts: list[dict] = []  # 수정분 상태와 번호 조회 결과가 어긋난 공고(사라짐으로 확정하지 않음)
    lookups_used = conflicting = 0
    if not aborted:
        sure, check = split_vanished(vanished, prev, partitions)
        confirmed.update({v: "complete_range" for v in sure})
        for n, v in enumerate(check):
            rows, s = fx.lookup_id(v, prev.get(v)) if n < args.max_lookups else (None, None)
            if s:
                lookup_params += s["request_params"]
                lookup_pages += s["page_count"]
                lookup_dropped += s["dropped_count"]
            state_text = rows[0].get("processState", "") if rows else ""
            if rows and state_text.startswith("종료"):
                confirmed[v] = "lookup"
                lookup_final[v] = rows[0]
                lookup_rows += rows
            elif rows and ("보호" in state_text or "공고" in state_text):
                still_protected[v] = prev[v]
                lookup_rows += rows
            else:  # 조회 실패, 0건(API 에서 사라졌거나 발생일이 틀림), 알 수 없는 상태
                reprocess.append(v)
        log(f"  사라진 공고 {len(vanished)}건: 구간 확정 {len(sure)} · 개별 조회 {len(check)}"
            f" -> 종료 확정 {len(confirmed) - len(sure)}, 아직 보호중 {len(still_protected)}, 재처리 {len(reprocess)}")
        for v in reprocess:
            for k in ("list_notice", "list_protect"):
                p = covering_partition(partitions, k, prev[v]) if prev.get(v) else None
                if p and not p["complete"]:
                    p["reprocess_ids"].append(v)
        if reprocess:
            fx.warnings.append(f"재처리 대상 {len(reprocess)}건: 기존 상태를 유지함")
        # 구간으로 확정했는데 같은 실행의 수정분 상태가 종료가 아니면, 수정 목록이 보호중 목록보다 늦게 반영된 것일 수 있다.
        # 수정분 상태를 쓰지 않고 번호로 한 번 조회해 실제 상태를 받는다(11/3 확인: 08:50 입양 종료가 수정 목록에는 보호중으로 남아 있었다).
        lookups_used = len(check)
        for v in [v for v, by in confirmed.items() if by == "complete_range"]:
            if not updated_by_id.get(v) or updated_by_id[v].get("processState", "").startswith("종료"):
                continue
            rows, s = fx.lookup_id(v, prev.get(v)) if lookups_used < args.max_lookups else (None, None)
            lookups_used += 1
            if s:
                lookup_params += s["request_params"]
                lookup_pages += s["page_count"]
                lookup_dropped += s["dropped_count"]
            state_text = rows[0].get("processState", "") if rows else ""
            if rows and ("보호" in state_text or "공고" in state_text):  # 목록과 조회가 어긋남: 종료로 확정하지 않고 다음 실행에서 다시 본다
                del confirmed[v]
                conflicting += 1
                conflicts.append({"event": "conflict", "desertionNo": v, "happenDt": prev.get(v), "detected_at": now.isoformat(timespec="seconds"),
                                  "list_state": updated_by_id[v].get("processState"), "lookup_state": state_text})
                still_protected[v] = prev[v]
                lookup_rows += rows
            else:  # 종료면 그 상태를 쓰고, 조회 실패나 0건이면 사라짐은 확정이므로 "종료(사유 미상)"으로 적는다
                relooked[v] = rows[0] if rows and state_text.startswith("종료") else None
                lookup_rows += rows if relooked[v] else []
        if relooked or conflicting:
            log(f"  수정분 상태가 종료가 아닌 사라진 공고 재조회: 종료 {sum(1 for r in relooked.values() if r)}"
                f", 사유 미상 {sum(1 for r in relooked.values() if not r)}, 목록과 어긋남(보호중 유지) {conflicting}")
    if args.lookup and confirmed and not aborted:
        days = sorted({prev[v] for v in confirmed if prev.get(v)})
        extra, s = fx.fetch("lookup", {}, [(d, d) for d in days])
        lookup_rows += extra
        lookup_params += s["request_params"]
        lookup_pages += s["page_count"]
        lookup_dropped += s["dropped_count"]
        log(f"  lookup: 발생일 {len(days)}개 -> {len(extra):,}건")
    if lookup_rows:
        kinds["lookup"] = (lookup_rows, {"request_params": lookup_params, "partitions": [], "page_count": lookup_pages,
                                         "dropped_count": lookup_dropped, "duplicate_count": 0})
    final_by_id = {**{i["desertionNo"]: i for i in lookup_rows}, **updated_by_id}

    events = []
    for v, by in confirmed.items():
        if v in relooked:
            final, source = relooked[v], "relookup"
        elif v in lookup_final:
            final, source = lookup_final[v], "lookup"
        else:
            final, source = final_by_id.get(v), "updated"
        if not final:  # 상태를 확인할 근거가 없어 사유 미상으로 적는다(추정)
            source = "none"
        events.append({"event": "vanished", "desertionNo": v, "happenDt": prev.get(v), "detected_at": now.isoformat(timespec="seconds"),
                       "confirmed_by": by, "state_source": source, "processState": final.get("processState") if final else "종료(사유 미상)"})
    events += conflicts
    for v in returned:
        events.append({"event": "returned", "desertionNo": v, "happenDt": protected.get(v), "detected_at": now.isoformat(timespec="seconds")})

    manifest = {
        "source": "data.go.kr 15098931", "endpoint": URL, "run_id": run_id, "fetched_at": now.isoformat(timespec="seconds"),
        "code_version": CODE_VERSION, "mode": "baseline" if first_run else "incremental", "status": "aborted" if aborted else "ok",
        "updated_range": [ymd(bgupd), ymd(today)], "calls": fx.calls, "warnings": fx.warnings, "files": [],
        "request_params": {k: s["request_params"] for k, (_, s) in kinds.items()},  # 인증키는 params 에 없다
        "partitions": partitions,  # 구간별 결과. B 의 validate_partition(**{result_code, start_date, end_date, total_count, received_count})
        "page_count": {k: s["page_count"] for k, (_, s) in kinds.items()},
        "dropped_count": sum(s["dropped_count"] for _, s in kinds.values()),
        "duplicate_count": sum(s["duplicate_count"] for _, s in kinds.values()),
        "vanished_detected": len(vanished), "confirmed_missing": len(confirmed),
        "confirmed_by": {by: sum(1 for b in confirmed.values() if b == by) for by in ("complete_range", "lookup")},
        "still_protected": len(still_protected), "reprocess_ids": reprocess, "returned": len(returned),
        "conflicting": conflicting, "lookup_count": min(lookups_used, args.max_lookups),
    }
    for w in fx.warnings:
        log(f"경고: {w}")

    if args.dry_run:
        log(f"dry-run: 호출 {fx.calls}번, 파일·상태는 쓰지 않음")
        return

    # 임시 폴더에 파일을 만들고 체크섬을 계산한 뒤 저장소에 올린다. manifest 는 마지막에 올린다.
    run_prefix = f"{RAW_PREFIX}/dt={today:%Y-%m-%d}/run_id={run_id}"
    with tempfile.TemporaryDirectory(prefix="rebom_rescue_") as tmp:
        staging = Path(tmp)
        for k, (rows, _) in kinds.items():
            manifest["files"].append({"kind": k, **write_jsonl_gz(staging / f"{k}.jsonl.gz", drop_fields(rows, DROP_FIELDS))})
        manifest["files"].append({"kind": "events", **write_jsonl_gz(staging / "events.jsonl.gz", events)})
        manifest["dropped_fields"] = list(DROP_FIELDS)
        for item in manifest["files"]:
            store.put_file(staging / item["file"], f"{run_prefix}/{item['file']}", checksum=item["checksum"])
        store.put_bytes(f"{run_prefix}/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"), overwrite=False)
    log(f"저장({store.kind}): {store.describe(run_prefix)}")

    if aborted:
        sys.exit(2)
    for v in confirmed:
        ended[v] = today.isoformat()
    for v in returned:
        ended.pop(v, None)
    # 목록에는 없지만 종료로 확정하지 못한 공고는 보호중으로 남겨 두어 다음 실행에서 다시 본다
    protected.update(still_protected)
    protected.update({v: prev[v] for v in reprocess})
    # 업로드가 모두 끝난 뒤에만 상태를 갱신한다
    save_state(store, {"last_enupd": ymd(today), "protected": protected, "ended": ended, "last_run_id": run_id})
    log(f"상태 갱신: last_enupd={ymd(today)}")


if __name__ == "__main__":
    try:
        main()
    except ApiFailure as e:
        sys.exit(f"수집 실패: {e}")
    except UploadError as e:
        sys.exit(f"업로드 실패: {e}")
