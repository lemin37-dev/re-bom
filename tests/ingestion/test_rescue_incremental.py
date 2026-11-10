"""증분 수집(rebom.ingestion.rescue_incremental, Bronze)과 사라짐 이벤트 생성(rebom.storage.silver.generate_ingest_events, Silver) 테스트.
실제 API 는 부르지 않고 가짜 응답을 쓴다."""
import gzip
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from rebom.ingestion import rescue_incremental as ri
from rebom.ingestion.rescue_api import ApiResult
from rebom.ingestion.storage import LocalStore
from rebom.storage.silver import generate_ingest_events as ge
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
    vanished, returned = ge.diff_states({"a": "", "b": "", "c": ""}, {"x": ""}, {"a", "c", "x", "n"})
    assert vanished == ["b"]
    assert returned == ["x"]


def test_split_vanished_needs_both_lists_complete():
    def part(kind, complete):
        return {"kind": kind, "start_date": "2026-10-01", "end_date": "2026-11-02", "complete": complete}

    prev = {"a": "20261010", "b": "20261011", "nodate": ""}
    both = [part("list_notice", True), part("list_protect", True)]
    assert ge.split_vanished(["a", "b"], prev, both) == (["a", "b"], [])
    one_bad = [part("list_notice", True), part("list_protect", False)]
    assert ge.split_vanished(["a", "b"], prev, one_bad) == ([], ["a", "b"])
    assert ge.split_vanished(["nodate"], prev, both) == ([], ["nodate"])


def make_fake_call(records, protect_extra, null_days=None, stale_updated=None, ended_updated=None, fail_lookup=None):
    """notice 는 비어 있고 protect 목록에는 current 만 있다. protect_extra 만큼 totalCount 를 크게 줘 불완전 구간을 만들고,
    null_days({발생일: 건수})는 그 날이 들어간 구간의 totalCount 에만 건수를 더한다(API 가 돌려주지 못하는 null 레코드).
    updated 에는 stale_updated({번호: 발생 며칠 전})의 공고를 보호중으로(수정 목록이 늦게 반영되는 경우),
    ended_updated({번호: (상태, 발생 며칠 전)})의 공고를 그 상태로 내려준다. fail_lookup 의 번호를 조회하면 호출이 실패한다."""
    current = [r for r in records if r["processState"] == "보호중" and not r.get("hidden")]

    def fake_call(params, url=None, timeout=0):
        p = dict(params)
        in_range = lambda r: p["bgnde"] <= r["happenDt"] <= p["endde"]
        if "desertion_no" in p:
            if p["desertion_no"] in (fail_lookup or ()):
                raise requests.ConnectionError("조회 실패")
            hit = [r for r in records if r["desertionNo"] == p["desertion_no"] and in_range(r)]
            return ApiResult(200, "00", len(hit), hit)
        state = p.get("state")
        if state == "notice":
            return ApiResult(200, "00", 0, [])
        if state == "protect":
            items = [r for r in current if in_range(r)]
            hidden = sum(n for day, n in (null_days or {}).items() if p["bgnde"] <= day <= p["endde"])
            return ApiResult(200, "00", len(items) + protect_extra + hidden, items)
        updated = [{"desertionNo": no, "happenDt": happen(days), "processState": "보호중"} for no, days in (stale_updated or {}).items()]
        updated += [{"desertionNo": no, "happenDt": happen(days), "processState": st} for no, (st, days) in (ended_updated or {}).items()]
        items = [r for r in updated if in_range(r)]
        return ApiResult(200, "00", len(items), items)

    return fake_call


def seed_scenario(store, protect_extra, null_days=None, stale_updated=None, ended_updated=None, fail_lookup=None):
    """수집 상태 파일(11/3 까지의 모양: 보호중·종료 목록 포함)에 보호중 33건(P00~P29, B, C, D)을 넣고,
    B·C·D 가 목록에서 사라진 하루를 만들 가짜 API 를 돌려준다."""
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
    return make_fake_call(records, protect_extra, null_days, stale_updated, ended_updated, fail_lookup)


def run_main(store, fake_call):
    """수집(Bronze)만 실행한다."""
    with patch.object(ri, "make_store", lambda root: store), patch.object(ri, "HAPPEN_START", TODAY - timedelta(days=20)), \
            patch.object(ri, "call", fake_call), patch.object(ri.time, "sleep", lambda s: None), \
            patch.object(sys, "argv", ["rescue_incremental"]):
        ri.main()


def run_silver(store, fake_call, *extra):
    """사라짐 이벤트 생성(Silver)만 실행한다. 마지막 Bronze 실행을 처리한다."""
    with patch.object(ge, "make_store", lambda root: store), patch.object(ri, "call", fake_call), \
            patch.object(ri.time, "sleep", lambda s: None), patch.object(sys, "argv", ["generate_ingest_events", *extra]):
        ge.main()


def run_once(protect_extra, null_days=None, stale_updated=None, ended_updated=None, fail_lookup=None, silver_args=()):
    tmp = Path(tempfile.mkdtemp(prefix="rescue_test_"))
    store = LocalStore(tmp)
    fake_call = seed_scenario(store, protect_extra, null_days, stale_updated, ended_updated, fail_lookup)
    run_main(store, fake_call)
    run_silver(store, fake_call, *silver_args)
    bronze_dir = next((tmp / "bronze" / "rescue" / "raw").glob("dt=*/run_id=*"))
    silver_dir = next((tmp / "silver" / "rescue" / "events").glob("dt=*/run_id=*"))
    return SimpleNamespace(
        store=store, tmp=tmp, bronze_dir=bronze_dir, silver_dir=silver_dir,
        bronze=json.loads((bronze_dir / "manifest.json").read_text(encoding="utf-8")),
        summary=json.loads((silver_dir / "manifest.json").read_text(encoding="utf-8")),
        events=[json.loads(line) for line in gzip.open(silver_dir / "events.jsonl.gz", "rt", encoding="utf-8")],
        state=json.loads(store.get_bytes(ge.STATE_KEY).decode("utf-8")),
    )


def by_id(events):
    return {e["desertionNo"]: e for e in events}


def test_bronze_saves_only_raw_files_and_keeps_other_state_keys():
    tmp = Path(tempfile.mkdtemp(prefix="rescue_test_"))
    store = LocalStore(tmp)
    run_main(store, seed_scenario(store, protect_extra=0))
    run_dir = next((tmp / "bronze" / "rescue" / "raw").glob("dt=*/run_id=*"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert [f["kind"] for f in manifest["files"]] == ["updated", "list_notice", "list_protect"]
    assert not (run_dir / "events.jsonl.gz").exists()
    assert not {"confirmed_missing", "vanished_detected", "confirmed_by"} & set(manifest)  # 해석 결과는 Bronze 에 없다
    assert all(p["complete"] for p in manifest["partitions"])
    state = json.loads(store.get_bytes(ri.STATE_KEY).decode("utf-8"))
    assert state["last_enupd"] == ri.ymd(TODAY) and state["last_run_id"] == manifest["run_id"]
    assert "protected" in state  # 이어받기 전의 보호중 목록은 지우지 않는다


def test_vanished_without_updated_evidence_is_looked_up_before_confirming():
    # 구간은 모두 완전하고 B, C, D 가 사라졌는데 수정분에는 셋 다 없다
    r = run_once(protect_extra=0)
    events = by_id(r.events)
    # B: 조회하니 종료(입양)
    assert events["B"]["state_source"] == "relookup" and events["B"]["processState"] == "종료(입양)"
    assert events["B"]["confirmed_by"] == "complete_range"
    # D: 조회해도 0건 -> 사라짐은 확정이지만 상태를 알 수 없다
    assert events["D"]["state_source"] == "none" and events["D"]["processState"] == "종료(사유 미상)"
    # C: 목록에서만 빠졌고 조회하면 아직 보호중 -> 확정하지 않고 충돌로 남긴다 (11/3 에 일시적으로 목록에서 빠진 6건과 같은 경우)
    assert "C" not in events
    assert [(c["desertionNo"], c["list_state"], c["lookup_state"]) for c in r.summary["conflicts"]] == [("C", None, "보호중")]
    assert "C" in r.state["protected"] and "C" not in r.state["ended"]
    assert sorted(r.state["ended"]) == ["B", "D"]
    assert r.summary["confirmed_missing"] == 2 and r.summary["conflicting"] == 1 and r.summary["lookup_count"] == 3
    assert r.summary["confirmed_by"] == {"complete_range": 2, "lookup": 0}
    assert all(e["event"] == "vanished" for e in r.events)  # conflict 는 이벤트가 아니라 요약에만 남는다


def test_updated_closed_state_confirms_without_lookup():
    r = run_once(protect_extra=0, ended_updated={"B": ("종료(입양)", 5)})
    b = by_id(r.events)["B"]
    assert (b["confirmed_by"], b["state_source"], b["processState"]) == ("complete_range", "updated", "종료(입양)")
    assert r.summary["lookup_count"] == 2  # 근거가 없는 C, D 만 조회했다


def test_incomplete_partition_falls_back_to_one_by_one_lookup():
    # B(5일 전), C(6일 전), D(7일 전)가 발생한 날마다 API 가 돌려주지 못하는 레코드가 1건씩 있다
    r = run_once(protect_extra=0, null_days={happen(5): 1, happen(6): 1, happen(7): 1})
    protect = next(p for p in r.bronze["partitions"] if p["kind"] == "list_protect" and p["start_date"] == ri.dash(happen(7)))
    assert protect["complete"] is False and protect["reason"] == "count_mismatch"
    assert r.summary["confirmed_by"] == {"complete_range": 0, "lookup": 1}
    assert [(e["desertionNo"], e["confirmed_by"], e["state_source"], e["processState"]) for e in r.events] == [("B", "lookup", "lookup", "종료(입양)")]
    assert r.summary["still_protected"] == 1  # C
    assert r.summary["reprocess_ids"] == ["D"]
    assert protect["reprocess_ids"] == []  # Bronze manifest 는 수집 뒤에 바뀌지 않는다
    assert r.summary["lookup_count"] == 3 and r.summary["conflicting"] == 0
    # 확정하지 못한 C, D 는 보호중으로 남아 다음 실행에서 다시 본다
    assert "C" in r.state["protected"] and "D" in r.state["protected"]
    assert "B" not in r.state["protected"] and list(r.state["ended"]) == ["B"]


def test_null_record_day_is_narrowed_so_only_that_day_needs_lookup():
    # D 가 발생한 날(7일 전)에만 API 가 돌려주지 못하는 레코드가 1건 있다
    r = run_once(protect_extra=0, null_days={happen(7): 1})
    protect = sorted((p for p in r.bronze["partitions"] if p["kind"] == "list_protect"), key=lambda p: p["start_date"])

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

    # B(5일 전), C(6일 전)는 완전한 구간이라 번호 조회는 종료 근거가 없을 때만 하고, 불완전한 날에 발생한 D 만 구간으로 확정할 수 없다
    assert r.summary["confirmed_by"] == {"complete_range": 1, "lookup": 0}  # B
    assert r.summary["conflicting"] == 1  # C
    assert r.summary["reprocess_ids"] == ["D"]


def test_vanished_with_non_ended_updated_state_is_looked_up_again():
    # 구간은 모두 완전하고 B, C, D 가 사라졌는데, 같은 실행의 수정분에는 셋 다 보호중으로 남아 있다
    r = run_once(protect_extra=0, stale_updated={"B": 5, "C": 6, "D": 7})
    events = by_id(r.events)
    # B: 조회하니 종료(입양) -> 수정분의 보호중이 아니라 조회한 상태를 쓴다
    assert events["B"]["processState"] == "종료(입양)" and events["B"]["state_source"] == "relookup"
    # D: 조회해도 0건 -> 사라짐은 확정이므로 사유 미상
    assert events["D"]["processState"] == "종료(사유 미상)" and events["D"]["state_source"] == "none"
    # C: 조회하니 아직 보호중 -> 종료로 확정하지 않고 보호중으로 남기며, 어긋난 두 상태를 번호별로 기록한다
    assert "C" not in events and "C" in r.state["protected"] and "C" not in r.state["ended"]
    assert [(c["desertionNo"], c["list_state"], c["lookup_state"]) for c in r.summary["conflicts"]] == [("C", "보호중", "보호중")]
    assert sorted(r.state["ended"]) == ["B", "D"]
    assert r.summary["still_protected"] == 1 and r.summary["confirmed_missing"] == 2
    assert r.summary["confirmed_by"] == {"complete_range": 2, "lookup": 0}
    assert r.summary["conflicting"] == 1 and r.summary["lookup_count"] == 3


def test_failed_lookup_does_not_confirm_the_closure():
    r = run_once(protect_extra=0, fail_lookup={"B"})
    assert "B" not in by_id(r.events)
    assert r.summary["lookup_deferred_ids"] == ["B"]
    assert "B" in r.state["protected"] and "B" not in r.state["ended"]  # 확인하지 못했으니 보호중으로 두고 다음에 다시 본다


def test_lookup_limit_defers_the_rest_instead_of_confirming():
    r = run_once(protect_extra=0, silver_args=("--max-lookups", "1"))
    assert list(by_id(r.events)) == ["B"]  # 번호순으로 첫 조회만 했다
    assert r.summary["lookup_deferred_ids"] == ["C", "D"] and r.summary["lookup_count"] == 1
    assert {"C", "D"} <= set(r.state["protected"])


def test_same_bronze_run_is_not_processed_twice():
    r = run_once(protect_extra=0)
    with pytest.raises(SystemExit) as e:
        run_silver(r.store, make_fake_call([], 0))
    assert "이미 처리한" in str(e.value)


def test_first_silver_run_without_any_state_is_a_baseline():
    tmp = Path(tempfile.mkdtemp(prefix="rescue_test_"))
    store = LocalStore(tmp)
    fake_call = make_fake_call([rec(f"P{i:02d}", 3 + i % 10, "보호중") for i in range(30)], 0)
    run_main(store, fake_call)
    run_silver(store, fake_call)
    silver_dir = next((tmp / "silver" / "rescue" / "events").glob("dt=*/run_id=*"))
    assert [json.loads(line) for line in gzip.open(silver_dir / "events.jsonl.gz", "rt", encoding="utf-8")] == []
    state = json.loads(store.get_bytes(ge.STATE_KEY).decode("utf-8"))
    assert len(state["protected"]) == 30 and state["ended"] == {}


def test_owner_name_is_not_saved():
    r = run_once(protect_extra=0)
    assert r.bronze["dropped_fields"] == ["careOwnerNm"]
    lookup = [json.loads(line) for line in gzip.open(r.silver_dir / "lookup.jsonl.gz", "rt", encoding="utf-8")]
    assert lookup and all("careOwnerNm" not in row for row in lookup)
    for path in r.bronze_dir.glob("*.jsonl.gz"):
        assert all("careOwnerNm" not in json.loads(line) for line in gzip.open(path, "rt", encoding="utf-8"))
