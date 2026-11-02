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


def make_fake_call(records, protect_extra):
    """notice 는 비어 있고 protect 목록에는 current 만 있다. protect_extra 만큼 totalCount 를 크게 줘 불완전 구간을 만든다."""
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
            return ApiResult(200, "00", len(items) + protect_extra, items)
        return ApiResult(200, "00", 0, [])  # updated

    return fake_call


def run_once(protect_extra):
    """상태에 보호중 33건(P00~P29, B, C, D)이 있고 B·C·D 가 목록에서 사라진 하루를 돌린다."""
    tmp = Path(tempfile.mkdtemp(prefix="rescue_test_"))
    prev = {f"P{i:02d}": happen(3 + i % 10) for i in range(30)}
    prev.update({"B": happen(5), "C": happen(6), "D": happen(7)})
    (tmp / "state.json").write_text(
        json.dumps({"last_enupd": ri.ymd(TODAY - timedelta(days=1)), "protected": prev, "ended": {}}), encoding="utf-8"
    )
    records = [rec(k, 3 + i % 10, "보호중") for i, k in enumerate(k for k in prev if k not in ("B", "C", "D"))]
    # B 는 종료, C 는 아직 보호중인데 목록에서만 빠졌다, D 는 API 에서 없어졌다
    records += [rec("B", 5, "종료(입양)"), {**rec("C", 6, "보호중"), "hidden": True}]
    for r in records:
        r.setdefault("happenDt", happen(5))
    with patch.object(ri, "ROOT", tmp), patch.object(ri, "BRONZE", tmp / "raw"), patch.object(ri, "STATE_PATH", tmp / "state.json"), \
            patch.object(ri, "HAPPEN_START", TODAY - timedelta(days=20)), patch.object(ri, "call", make_fake_call(records, protect_extra)), \
            patch.object(ri.time, "sleep", lambda s: None), patch.object(sys, "argv", ["rescue_incremental"]):
        ri.main()
    run_dir = next((tmp / "raw").glob("dt=*/run_id=*"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in gzip.open(run_dir / "events.jsonl.gz", "rt", encoding="utf-8")]
    state = json.loads((tmp / "state.json").read_text(encoding="utf-8"))
    return run_dir, manifest, events, state


def test_complete_partitions_confirm_vanished_by_range():
    _, manifest, events, state = run_once(protect_extra=0)
    assert all(p["complete"] for p in manifest["partitions"])
    assert manifest["confirmed_missing"] == 3
    assert manifest["confirmed_by"] == {"complete_range": 3, "lookup": 0}
    assert sorted(e["desertionNo"] for e in events) == ["B", "C", "D"]
    assert {e["confirmed_by"] for e in events} == {"complete_range"}
    assert sorted(state["ended"]) == ["B", "C", "D"]


def test_incomplete_partition_falls_back_to_one_by_one_lookup():
    _, manifest, events, state = run_once(protect_extra=1)
    protect = [p for p in manifest["partitions"] if p["kind"] == "list_protect"][0]
    assert protect["complete"] is False and protect["reason"] == "count_mismatch"
    assert manifest["confirmed_by"] == {"complete_range": 0, "lookup": 1}
    assert [(e["desertionNo"], e["confirmed_by"], e["processState"]) for e in events] == [("B", "lookup", "종료(입양)")]
    assert manifest["still_protected"] == 1  # C
    assert manifest["reprocess_ids"] == ["D"]
    assert protect["reprocess_ids"] == ["D"]
    # 확정하지 못한 C, D 는 보호중으로 남아 다음 실행에서 다시 본다
    assert "C" in state["protected"] and "D" in state["protected"]
    assert "B" not in state["protected"] and list(state["ended"]) == ["B"]


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
