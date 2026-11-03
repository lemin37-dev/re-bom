"""증분 수집(rebom.ingestion.rescue_incremental) 테스트. 실제 API 는 부르지 않고 가짜 응답을 쓴다."""
import gzip
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from rebom.ingestion import rescue_incremental as ri
from rebom.ingestion.rescue_api import ApiResult
from rebom.ingestion.storage import LocalStore
from rebom.storage.silver.validate_manifest import validate_partition

TODAY = date.today()


def happen(days_ago):
    return ri.ymd(TODAY - timedelta(days=days_ago))


def rec(no, days_ago, state):
    return {"desertionNo": no, "happenDt": happen(days_ago), "processState": state, "careOwnerNm": "홍길동"}


def test_happen_ranges_are_contiguous_and_short():
    ranges = ri.happen_ranges(date(2008, 1, 1), date(2026, 11, 2))
    assert ranges[0][0] == "20080101"
    assert ranges[-1][1] == "20261102"
    for (_, prev_end), (next_start, _) in zip(ranges, ranges[1:]):
        assert ri.parse_ymd(next_start) - ri.parse_ymd(prev_end) == timedelta(days=1)
    for start, end in ranges:
        assert (ri.parse_ymd(end) - ri.parse_ymd(start)).days + 1 <= ri.MAX_SPAN_DAYS


def test_partition_status_matches_b_validate_partition():
    cases = [
        ("00", "2023-01-01", "2025-12-30", 4796, 4796),
        ("00", "2023-01-01", "2025-12-30", 4796, 4793),
        ("99", "2026-01-01", "2026-01-02", 10, 10),
        ("00", "2020-01-01", "2023-01-01", 10, 10),
        ("00", "2010-01-01", "2010-12-31", 0, 0),
        ("00", "2026-01-01", "2026-01-02", 10, -1),
        ("00", "2026-01-01", "2026-01-02", None, 0),
        ("00", "20260101", "2026-01-02", 1, 1),
    ]
    for args in cases:
        assert ri.partition_status(*args) == validate_partition(*args), args


def test_diff_states():
    vanished, returned = ri.diff_states({"a": "", "b": "", "c": ""}, {"x": ""}, {"a", "c", "x", "n"})
    assert vanished == ["b"]
    assert returned == ["x"]


def test_split_vanished_needs_both_lists_complete():
    def part(kind, complete):
        return {"kind": kind, "start_date": "2026-10-01", "end_date": "2026-11-02", "complete": complete}

    prev = {"a": "20261010", "b": "20261011", "nodate": ""}
    both = [part("list_notice", True), part("list_protect", True)]
    assert ri.split_vanished(["a", "b"], prev, both) == (["a", "b"], [])
    one_bad = [part("list_notice", True), part("list_protect", False)]
    assert ri.split_vanished(["a", "b"], prev, one_bad) == ([], ["a", "b"])
    assert ri.split_vanished(["nodate"], prev, both) == ([], ["nodate"])


def make_fake_call(records, protect_extra, null_days=None, stale_updated=None):
    """notice 는 비어 있고 protect 목록에는 current 만 있다. protect_extra 만큼 totalCount 를 크게 줘 불완전 구간을 만들고,
    null_days({발생일: 건수})는 그 날이 들어간 구간의 totalCount 에만 건수를 더한다(API 가 돌려주지 못하는 null 레코드)."""
    current = [r for r in records if r["processState"] == "보호중" and not r.get("hidden")]

    def fake_call(params, url=None, timeout=0):
        p = dict(params)
        in_range = lambda r: p["bgnde"] <= r["happenDt"] <= p["endde"]
        if "desertion_no" in p:
            hit = [r for r in records if r["desertionNo"] == p["desertion_no"] and in_range(r)]
            return ApiResult(200, "00", len(hit), hit)
        state = p.get("state")
        if state == "notice":
            return ApiResult(200, "00", 0, [])
        if state == "protect":
            items = [r for r in current if in_range(r)]
            hidden = sum(n for day, n in (null_days or {}).items() if p["bgnde"] <= day <= p["endde"])
            return ApiResult(200, "00", len(items) + protect_extra + hidden, items)
        # updated: stale_updated({번호: 발생 며칠 전})의 공고는 실제 상태와 상관없이 보호중으로 내려준다(수정 목록이 늦게 반영되는 경우)
        stale = [{"desertionNo": no, "happenDt": happen(days), "processState": "보호중"} for no, days in (stale_updated or {}).items()]
        items = [r for r in stale if in_range(r)]
        return ApiResult(200, "00", len(items), items)

    return fake_call


def seed_scenario(store, protect_extra, null_days=None, stale_updated=None):
    """상태에 보호중 33건(P00~P29, B, C, D)을 넣고, B·C·D 가 목록에서 사라진 하루를 만들 가짜 API 를 돌려준다."""
    prev = {f"P{i:02d}": happen(3 + i % 10) for i in range(30)}
    prev.update({"B": happen(5), "C": happen(6), "D": happen(7)})
    store.put_bytes(
        ri.STATE_KEY,
        json.dumps({"last_enupd": ri.ymd(TODAY - timedelta(days=1)), "protected": prev, "ended": {}}).encode("utf-8"),
    )
    records = [rec(k, 3 + i % 10, "보호중") for i, k in enumerate(k for k in prev if k not in ("B", "C", "D"))]
    # B 는 종료, C 는 아직 보호중인데 목록에서만 빠졌다, D 는 API 에서 없어졌다
    records += [rec("B", 5, "종료(입양)"), {**rec("C", 6, "보호중"), "hidden": True}]
    for r in records:
        r.setdefault("happenDt", happen(5))
    return make_fake_call(records, protect_extra, null_days, stale_updated)


def run_main(store, fake_call):
    with patch.object(ri, "make_store", lambda root: store), patch.object(ri, "HAPPEN_START", TODAY - timedelta(days=20)), \
            patch.object(ri, "call", fake_call), patch.object(ri.time, "sleep", lambda s: None), \
            patch.object(sys, "argv", ["rescue_incremental"]):
        ri.main()


def run_once(protect_extra, null_days=None):
    tmp = Path(tempfile.mkdtemp(prefix="rescue_test_"))
    store = LocalStore(tmp)
    run_main(store, seed_scenario(store, protect_extra, null_days))
    run_dir = next((tmp / "bronze" / "rescue" / "raw").glob("dt=*/run_id=*"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in gzip.open(run_dir / "events.jsonl.gz", "rt", encoding="utf-8")]
    state = json.loads(store.get_bytes(ri.STATE_KEY).decode("utf-8"))
    return run_dir, manifest, events, state


def test_complete_partitions_confirm_vanished_by_range():
    _, manifest, events, state = run_once(protect_extra=0)
    assert all(p["complete"] for p in manifest["partitions"])
    assert manifest["confirmed_missing"] == 3
    assert manifest["confirmed_by"] == {"complete_range": 3, "lookup": 0}
    assert sorted(e["desertionNo"] for e in events) == ["B", "C", "D"]
    assert {e["confirmed_by"] for e in events} == {"complete_range"}
    assert {e["state_source"] for e in events} == {"none"}  # 수정분에도 없어 상태를 확인할 근거가 없다(사유 미상)
    assert {e["processState"] for e in events} == {"종료(사유 미상)"}
    assert sorted(state["ended"]) == ["B", "C", "D"]


def test_incomplete_partition_falls_back_to_one_by_one_lookup():
    # B(5일 전), C(6일 전), D(7일 전)가 발생한 날마다 API 가 돌려주지 못하는 레코드가 1건씩 있다
    _, manifest, events, state = run_once(protect_extra=0, null_days={happen(5): 1, happen(6): 1, happen(7): 1})
    protect = next(p for p in manifest["partitions"] if p["kind"] == "list_protect" and p["start_date"] == ri.dash(happen(7)))
    assert protect["complete"] is False and protect["reason"] == "count_mismatch"
    assert manifest["confirmed_by"] == {"complete_range": 0, "lookup": 1}
    assert [(e["desertionNo"], e["confirmed_by"], e["processState"]) for e in events] == [("B", "lookup", "종료(입양)")]
    assert events[0]["state_source"] == "lookup" and manifest["lookup_count"] == 3 and manifest["conflicting"] == 0
    assert manifest["still_protected"] == 1  # C
    assert manifest["reprocess_ids"] == ["D"]
    assert protect["reprocess_ids"] == ["D"]
    # 확정하지 못한 C, D 는 보호중으로 남아 다음 실행에서 다시 본다
    assert "C" in state["protected"] and "D" in state["protected"]
    assert "B" not in state["protected"] and list(state["ended"]) == ["B"]


def test_null_record_day_is_narrowed_so_only_that_day_needs_lookup():
    tmp = Path(tempfile.mkdtemp(prefix="rescue_test_"))
    store = LocalStore(tmp)
    # D 가 발생한 날(7일 전)에만 API 가 돌려주지 못하는 레코드가 1건 있다
    run_main(store, seed_scenario(store, protect_extra=0, null_days={happen(7): 1}))
    manifest = json.loads(next((tmp / "bronze" / "rescue" / "raw").glob("dt=*/run_id=*/manifest.json")).read_text(encoding="utf-8"))
    protect = sorted((p for p in manifest["partitions"] if p["kind"] == "list_protect"), key=lambda p: p["start_date"])

    bad = [p for p in protect if not p["complete"]]
    day = ri.dash(happen(7))
    assert [(p["start_date"], p["end_date"], p["reason"]) for p in bad] == [(day, day, "count_mismatch")]
    # 쪼개진 구간들은 서로 겹치지 않고 빈틈 없이 이어진다 (B 의 can_confirm_missing 이 구간 중복을 오류로 본다)
    assert protect[0]["start_date"] == ri.dash(ri.ymd(TODAY - timedelta(days=20)))
    assert protect[-1]["end_date"] == ri.dash(ri.ymd(TODAY))
    for before, after in zip(protect, protect[1:]):
        assert date.fromisoformat(after["start_date"]) - date.fromisoformat(before["end_date"]) == timedelta(days=1)
    for p in protect:
        assert validate_partition(p["result_code"], p["start_date"], p["end_date"], p["total_count"], p["received_count"]) == (p["complete"], p["reason"])

    # B(5일 전), C(6일 전)는 완전한 구간이라 개별 조회 없이 확정되고, 불완전한 날에 발생한 D 만 개별 조회 대상이 된다
    assert manifest["confirmed_by"] == {"complete_range": 2, "lookup": 0}
    assert manifest["reprocess_ids"] == ["D"]


def test_vanished_with_non_ended_updated_state_is_looked_up_again():
    # 구간은 모두 완전하고 B, C, D 가 사라졌는데, 같은 실행의 수정분에는 셋 다 보호중으로 남아 있다
    tmp = Path(tempfile.mkdtemp(prefix="rescue_test_"))
    store = LocalStore(tmp)
    run_main(store, seed_scenario(store, protect_extra=0, stale_updated={"B": 5, "C": 6, "D": 7}))
    run_dir = next((tmp / "bronze" / "rescue" / "raw").glob("dt=*/run_id=*"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    events = {e["desertionNo"]: e for e in (json.loads(l) for l in gzip.open(run_dir / "events.jsonl.gz", "rt", encoding="utf-8"))}
    state = json.loads(store.get_bytes(ri.STATE_KEY).decode("utf-8"))

    # B: 조회하니 종료(입양) -> 수정분의 보호중이 아니라 조회한 상태를 쓴다
    assert events["B"]["processState"] == "종료(입양)" and events["B"]["confirmed_by"] == "complete_range"
    assert events["B"]["state_source"] == "relookup"
    # D: 조회해도 0건 -> 사라짐은 확정이므로 사유 미상(근거 없는 추정)
    assert events["D"]["processState"] == "종료(사유 미상)" and events["D"]["state_source"] == "none"
    # C: 조회하니 아직 보호중 -> 종료로 확정하지 않고 보호중으로 남기고, 어긋남을 번호별로 기록한다
    assert events["C"] == {"event": "conflict", "desertionNo": "C", "happenDt": happen(6), "detected_at": events["C"]["detected_at"],
                           "list_state": "보호중", "lookup_state": "보호중"}
    assert "C" in state["protected"] and "C" not in state["ended"]
    assert sorted(state["ended"]) == ["B", "D"]
    assert manifest["still_protected"] == 1 and manifest["confirmed_missing"] == 2
    assert manifest["conflicting"] == 1 and manifest["lookup_count"] == 3
    assert manifest["confirmed_by"] == {"complete_range": 2, "lookup": 0}


def test_owner_name_is_not_saved():
    run_dir, manifest, _, _ = run_once(protect_extra=0)
    assert manifest["dropped_fields"] == ["careOwnerNm"]
    for f in run_dir.glob("*.jsonl.gz"):
        assert "careOwnerNm" not in gzip.open(f, "rt", encoding="utf-8").read()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"{name}: PASS")
