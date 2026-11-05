"""Silver 단계: Bronze 수집 실행을 읽어 '사라짐·복귀' 이벤트를 만든다. (구현: 차현지, A)

수집(Bronze, rebom.ingestion.rescue_incremental)은 API 응답을 그대로 저장하고 구간별 건수를 기록하는 데까지만 한다.
이 단계는 저장된 raw 파일을 읽어서 해석한다.
  ① 어제 보호중 목록과 오늘 목록(list_notice + list_protect)을 비교해 사라진 공고와 복귀한 공고를 찾는다.
  ② 사라짐을 확정한다. 그 공고의 발생일을 덮는 notice·protect 구간이 모두 완전하고(결과 코드 00, 1,095일 이하, 받은 건수 = totalCount)
     같은 실행의 수정분(updated)에 종료 상태가 있으면 그 상태로 바로 확정한다(confirmed_by=complete_range, state_source=updated).
  ③ 종료 근거가 없으면 확정하기 전에 desertion_no + 발생일로 한 건 조회한다.
       - 종료로 나오면 확정(state_source=relookup), 아직 보호중·공고중이면 확정하지 않고 충돌로 기록(보호중 유지)
       - 0건이면 사라짐은 확정하되 상태는 알 수 없으므로 종료(사유 미상)(state_source=none)
       - 호출이 실패했거나 조회 상한을 넘으면 확인하지 못한 것이므로 확정하지 않고 보호중으로 둔다(lookup_deferred_ids)
  ④ 구간이 불완전하면 같은 방식으로 번호 조회해서 확정한다(confirmed_by=lookup, state_source=lookup). 실패하면 reprocess_ids 에 남긴다.
  ⑤ 다시 나타난 공고는 returned 이벤트로 남긴다.

입력:  bronze/rescue/raw/dt=<날짜>/run_id=<id>/ 의 updated / list_notice / list_protect .jsonl.gz, manifest.json (체크섬을 다시 확인한다)
출력:  silver/rescue/events/dt=<날짜>/run_id=<같은 id>/ 의 events.jsonl.gz, lookup.jsonl.gz(조회했을 때만, 조회 근거), manifest.json
상태:  state/rescue_events_state.json (보호중 목록, 종료로 확정한 공고, 마지막으로 처리한 Bronze run_id)
       이 파일이 없으면 11/3 까지 수집 상태 파일(state/rescue_state.json)에 있던 보호중·종료 목록을 이어받는다.
올리는 순서: events -> lookup -> manifest.json(마지막, 덮어쓰기 금지) -> 상태 파일. 업로드가 하나라도 실패하면 상태를 갱신하지 않는다.

사용 예 (레포 루트에서, 수집을 먼저 실행한 뒤):
  PYTHONPATH=src python -m rebom.storage.silver.generate_ingest_events --dry-run   # 계산하고 조회만, 파일·상태는 쓰지 않음
  PYTHONPATH=src python -m rebom.storage.silver.generate_ingest_events             # 마지막 Bronze 실행을 처리
  PYTHONPATH=src python -m rebom.storage.silver.generate_ingest_events --run-id 20261103T090045-1fe465

사라짐 이벤트의 필드와 B 가 읽는 규칙은 docs/data_contract.md 3절. 번호 조회는 수집의 Fetcher 를 그대로 쓰고,
조회 응답은 이 단계의 출력 폴더에 근거로 둔다(Bronze 실행 폴더는 manifest 를 마지막에 올린 뒤에는 바꾸지 않는다).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from rebom.ingestion import rescue_incremental as ri
from rebom.ingestion.rescue_incremental import (
    DATA_DIR,
    DROP_FIELDS,
    KST,
    RAW_PREFIX,
    ApiFailure,
    Fetcher,
    dash,
    drop_fields,
    log,
    write_jsonl_gz,
)
from rebom.ingestion.storage import UploadError, make_store

SILVER_PREFIX = "silver/rescue/events"
STATE_KEY = "state/rescue_events_state.json"
MAX_VANISH_RATIO = 0.2  # 계약에는 없는 추가 안전장치: 보호중 목록에서 한 번에 이만큼 넘게 사라지면 API 이상으로 보고 중단
MAX_LOOKUP_IDS = 300  # 한 번에 번호로 조회하는 상한. 넘는 공고는 확인하지 못한 것으로 두고 보호중으로 남긴다
MAX_CALLS = 400
CODE_VERSION = "2026-11-03"
REQUIRED_KINDS = ("updated", "list_notice", "list_protect")


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
    """(구간 판정으로 확정할 수 있는 공고, 한 건씩 확인이 필요한 공고). 발생일을 덮는 notice·protect 구간이 모두 완전해야 한다.
    notice 에서 protect 로 옮겨 갔을 수도 있어 둘 다 본다. 발생일을 모르면 확인이 필요한 쪽에 둔다."""
    sure, check = [], []
    for v in vanished:
        happen = prev_protected.get(v)
        cover = [covering_partition(partitions, k, happen) for k in ("list_notice", "list_protect")] if happen else []
        (sure if cover and all(p and p["complete"] for p in cover) else check).append(v)
    return sure, check


def read_bronze_run(store, dt: str, run_id: str) -> tuple[dict, dict[str, list[dict]]]:
    """Bronze 실행 폴더에서 manifest 와 종류별 파일을 읽는다. 체크섬과 건수가 맞지 않으면 중단한다."""
    prefix = f"{RAW_PREFIX}/dt={dt}/run_id={run_id}"
    raw = store.get_bytes(f"{prefix}/manifest.json")
    if raw is None:
        sys.exit(f"Bronze manifest 가 없음: {store.describe(prefix)}")
    manifest = json.loads(raw.decode("utf-8"))
    rows: dict[str, list[dict]] = {}
    for item in manifest.get("files", []):
        data = store.get_bytes(f"{prefix}/{item['file']}")
        if data is None or hashlib.sha256(data).hexdigest() != item["checksum"]:
            sys.exit(f"Bronze 파일이 없거나 체크섬이 다름: {item['file']}")
        parsed = [json.loads(line) for line in gzip.decompress(data).decode("utf-8").splitlines() if line]
        if len(parsed) != item["record_count"]:
            sys.exit(f"Bronze 파일 건수가 manifest 와 다름: {item['file']}")
        rows[item["kind"]] = parsed
    missing = [k for k in REQUIRED_KINDS if k not in rows]
    if missing:
        sys.exit(f"Bronze 실행에 필요한 파일이 없음: {missing}")
    return manifest, rows


def load_state(store) -> dict:
    data = store.get_bytes(STATE_KEY)
    if data:
        return json.loads(data.decode("utf-8"))
    legacy = ri.load_state(store)  # 11/3 까지는 수집 상태 파일에 보호중·종료 목록이 있었다
    if "protected" in legacy:
        return {"protected": legacy["protected"], "ended": legacy.get("ended", {})}
    return {}


def save_state(store, state: dict) -> None:
    store.put_bytes(STATE_KEY, json.dumps(state, ensure_ascii=False).encode("utf-8"), overwrite=True)


def state_kind(text: str) -> str:
    """조회로 받은 상태 문자열을 closed / protecting / unknown 으로 나눈다."""
    if text.startswith("종료"):
        return "closed"
    if "보호" in text or "공고" in text:
        return "protecting"
    return "unknown"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", help="처리할 Bronze 실행의 run_id (기본: 수집 상태 파일의 마지막 run_id)")
    ap.add_argument("--max-lookups", type=int, default=MAX_LOOKUP_IDS, help="번호로 조회하는 상한")
    ap.add_argument("--max-calls", type=int, default=MAX_CALLS, help="이 단계에서 쓸 최대 호출 수")
    ap.add_argument("--dry-run", action="store_true", help="계산과 조회만 하고 파일·상태를 쓰지 않는다")
    args = ap.parse_args()

    now = datetime.now(KST)
    detected_at = now.isoformat(timespec="seconds")
    store = make_store(DATA_DIR)
    run_id = args.run_id or ri.load_state(store).get("last_run_id")
    if not run_id:
        sys.exit("처리할 Bronze 실행이 없음: 수집을 먼저 실행하세요")
    dt = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"
    bronze, rows = read_bronze_run(store, dt, run_id)
    if bronze.get("status") != "ok":
        sys.exit(f"Bronze 실행 상태가 ok 가 아님({bronze.get('status')}): {run_id}")

    state = load_state(store)
    if state.get("last_run_id") == run_id:
        sys.exit(f"이미 처리한 Bronze 실행: {run_id}")
    first_run = not state
    log(f"Bronze 실행 {run_id} · {'첫 실행(기준선만)' if first_run else '이전 목록과 비교'}")

    protected = {i["desertionNo"]: i.get("happenDt", "") for k in ("list_notice", "list_protect") for i in rows[k]}
    prev = state.get("protected", {})
    ended = state.get("ended", {})
    vanished, returned = ([], []) if first_run else diff_states(prev, ended, set(protected))
    partitions = bronze["partitions"]
    log(f"  보호중 {len(protected):,}건 (이전 {len(prev):,}) · 사라짐 {len(vanished)} · 복귀 {len(returned)}")

    # 보호중 목록이 한꺼번에 많이 사라지면 API 이상으로 보고 아무것도 쓰지 않는다.
    if prev and len(vanished) > len(prev) * MAX_VANISH_RATIO:
        log(f"중단: 보호중 {len(vanished)}/{len(prev)}건이 한꺼번에 사라짐. 파일과 상태를 쓰지 않음")
        sys.exit(2)

    fx = Fetcher(args.max_calls)
    updated_by_id = {i["desertionNo"]: i for i in rows["updated"]}
    confirmed: dict[str, str] = {}  # 공고번호 -> complete_range | lookup
    state_source: dict[str, str] = {}  # 공고번호 -> updated | lookup | relookup | none
    final: dict[str, dict] = {}  # 공고번호 -> 상태 값을 가져온 행
    still_protected: dict[str, str] = {}  # 확정하지 않고 보호중으로 둔 공고 (번호 -> 발생일)
    reprocess: list[str] = []  # 구간이 불완전한데 번호 조회도 확인하지 못한 공고
    deferred: list[str] = []  # 구간은 완전하지만 종료 근거를 확인하지 못해 확정을 미룬 공고(호출 실패, 조회 상한)
    conflicts: list[dict] = []
    lookup_rows: list[dict] = []
    lookup_params: list[dict] = []
    lookup_pages = lookup_dropped = used = 0

    def look(v: str):
        """번호로 한 건 조회한다. 호출이 실패하면 None, 0건이면 빈 목록."""
        nonlocal used, lookup_pages, lookup_dropped
        used += 1
        got, s = fx.lookup_id(v, prev.get(v))
        if s:
            lookup_params.extend(s["request_params"])
            lookup_pages += s["page_count"]
            lookup_dropped += s["dropped_count"]
        return got

    sure, check = split_vanished(vanished, prev, partitions)
    # 구간이 불완전한 공고: 번호로 조회해서 종료를 확인한다
    for v in check:
        got = look(v) if used < args.max_lookups else None
        text = got[0].get("processState", "") if got else ""
        kind = state_kind(text)
        if got and kind == "closed":
            confirmed[v], state_source[v], final[v] = "lookup", "lookup", got[0]
            lookup_rows += got
        elif got and kind == "protecting":
            still_protected[v] = prev[v]
            lookup_rows += got
        else:  # 조회 실패, 0건, 알 수 없는 상태, 조회 상한 초과
            reprocess.append(v)
    # 구간이 완전한 공고: 수정분에 종료 상태가 있으면 그대로, 근거가 없으면 확정하기 전에 번호로 조회한다
    for v in sure:
        row = updated_by_id.get(v)
        if row and row.get("processState", "").startswith("종료"):
            confirmed[v], state_source[v], final[v] = "complete_range", "updated", row
            continue
        if used >= args.max_lookups:
            deferred.append(v)
            continue
        got = look(v)
        if got is None:  # 호출 실패: 확인하지 못했으니 확정하지 않는다
            deferred.append(v)
            continue
        text = got[0].get("processState", "") if got else ""
        kind = state_kind(text)
        if kind == "protecting":  # 목록에서는 사라졌는데 조회하면 아직 있다: 확정하지 않고 충돌로 남긴다
            conflicts.append({
                "desertionNo": v, "happenDt": prev.get(v), "detected_at": detected_at,
                "list_state": row.get("processState") if row else None, "lookup_state": text,
            })
            still_protected[v] = prev[v]
            lookup_rows += got
        elif kind == "closed":
            confirmed[v], state_source[v], final[v] = "complete_range", "relookup", got[0]
            lookup_rows += got
        else:  # 0건이거나 알 수 없는 상태: 사라짐은 구간으로 확정, 상태는 알 수 없음
            confirmed[v], state_source[v] = "complete_range", "none"
    log(f"  사라진 공고 {len(vanished)}건: 종료 확정 {len(confirmed)} · 보호중 유지 {len(still_protected)} (충돌 {len(conflicts)})"
        f" · 확정 미룸 {len(deferred)} · 재처리 {len(reprocess)} · 조회 {used}번")
    if reprocess:
        fx.warnings.append(f"재처리 대상 {len(reprocess)}건: 구간이 불완전한데 번호 조회로 확인하지 못해 기존 상태를 유지함")
    if deferred:
        fx.warnings.append(f"확정 미룸 {len(deferred)}건: 종료 근거를 확인하지 못해(호출 실패 또는 조회 상한) 보호중으로 둠")
    if conflicts:
        fx.warnings.append(f"충돌 {len(conflicts)}건: 목록에서 사라졌지만 번호 조회로는 아직 보호중이라 확정하지 않음")

    events = []
    for v, by in confirmed.items():
        events.append({
            "event": "vanished", "desertionNo": v, "happenDt": prev.get(v), "detected_at": detected_at,
            "confirmed_by": by, "state_source": state_source[v],
            "processState": final[v].get("processState") if v in final else "종료(사유 미상)",
        })
    for v in returned:
        events.append({"event": "returned", "desertionNo": v, "happenDt": protected.get(v), "detected_at": detected_at})

    summary = {
        "source": "silver: rescue ingest events", "bronze_run_id": run_id, "bronze_fetched_at": bronze.get("fetched_at"),
        "generated_at": detected_at, "code_version": CODE_VERSION, "status": "ok", "calls": fx.calls,
        "warnings": fx.warnings, "files": [], "request_params": {"lookup": lookup_params},
        "page_count": {"lookup": lookup_pages}, "dropped_count": lookup_dropped,
        "vanished_detected": len(vanished), "confirmed_missing": len(confirmed),
        "confirmed_by": {by: sum(1 for b in confirmed.values() if b == by) for by in ("complete_range", "lookup")},
        "still_protected": len(still_protected), "reprocess_ids": reprocess, "lookup_deferred_ids": deferred,
        "returned": len(returned), "conflicting": len(conflicts), "conflicts": conflicts, "lookup_count": used,
    }
    for w in fx.warnings:
        log(f"경고: {w}")

    if args.dry_run:
        log(f"dry-run: 호출 {fx.calls}번, 파일·상태는 쓰지 않음")
        return

    out_prefix = f"{SILVER_PREFIX}/dt={dt}/run_id={run_id}"
    with tempfile.TemporaryDirectory(prefix="rebom_events_") as tmp:
        staging = Path(tmp)
        summary["files"].append({"kind": "events", **write_jsonl_gz(staging / "events.jsonl.gz", events)})
        if lookup_rows:
            summary["files"].append({"kind": "lookup", **write_jsonl_gz(staging / "lookup.jsonl.gz", drop_fields(lookup_rows, DROP_FIELDS))})
        summary["dropped_fields"] = list(DROP_FIELDS)
        for item in summary["files"]:
            store.put_file(staging / item["file"], f"{out_prefix}/{item['file']}", checksum=item["checksum"])
        store.put_bytes(f"{out_prefix}/manifest.json", json.dumps(summary, ensure_ascii=False, indent=1).encode("utf-8"), overwrite=False)
    log(f"저장({store.kind}): {store.describe(out_prefix)}")

    for v in confirmed:
        ended[v] = dt
    for v in returned:
        ended.pop(v, None)
    # 목록에는 없지만 종료로 확정하지 못한 공고는 보호중으로 남겨 다음 실행에서 다시 본다
    protected.update(still_protected)
    protected.update({v: prev[v] for v in reprocess + deferred})
    # 업로드가 모두 끝난 뒤에만 상태를 갱신한다
    save_state(store, {"protected": protected, "ended": ended, "last_run_id": run_id})
    log("상태 갱신: " + STATE_KEY)


if __name__ == "__main__":
    try:
        main()
    except ApiFailure as e:
        sys.exit(f"조회 실패: {e}")
    except UploadError as e:
        sys.exit(f"업로드 실패: {e}")
