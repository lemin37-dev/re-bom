import json
from collections import Counter
from pathlib import Path

from rebom.storage.silver.transform_status_history import (
    transform_status_history,
)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    root = Path("data/sample/incremental")

    previous = load_json(root / "t0_2026-10-28.json")
    current = load_json(root / "truth_2026-10-30.json")

    events, stats = transform_status_history(
        previous,
        current,
        "2026-10-28",
        "2026-10-30",
    )

    event_counts = Counter(
        event["event_type"] for event in events
    )

    print("Previous records:", len(previous))
    print("Current records:", len(current))
    print("Initial baseline:", stats["initial"])
    print("New notices:", stats["new"])
    print("Changed states:", stats["changed"])
    print("Unchanged states:", stats["unchanged"])
    print("Not observed again:", stats["not_observed_again"])
    print("Total history events:", len(events))
    print("Event types:", dict(event_counts))

    # 생성된 이벤트가 통계와 일치하는지 확인한다.
    assert len(previous) == 87
    assert len(current) == 115
    assert event_counts["INITIAL"] == (
        stats["initial"] + stats["new"]
    )
    assert event_counts["CHANGED"] == stats["changed"]
    assert len(events) == (
        stats["initial"] + stats["new"] + stats["changed"]
    )

    # 동일 상태, 변경, 복귀, 재관측 누락을 따로 검사한다.
    sample_previous = [
        {"desertionNo": "A", "processState": "보호중"},
        {"desertionNo": "B", "processState": "종료(입양)"},
        {"desertionNo": "C", "processState": "보호중"},
        {"desertionNo": "D", "processState": "보호중"},
    ]

    sample_current = [
        {"desertionNo": "A", "processState": "보호중"},
        {"desertionNo": "B", "processState": "보호중"},
        {"desertionNo": "C", "processState": "종료(입양)"},
        {"desertionNo": "E", "processState": "종료(반환)"},
    ]

    test_events, test_stats = transform_status_history(
        sample_previous,
        sample_current,
        "2026-10-28",
        "2026-10-30",
    )


    # 기존 상태 변경 통계와 새로 추가된 추정 종료 통계를 함께 검증한다.
    # 이 테스트에서는 종료 확인 목록을 전달하지 않았으므로
    # 추정 종료 건수는 0이어야 한다.
    assert test_stats == {
        "initial": 4,
        "changed": 2,
        "unchanged": 1,
        "new": 1,
        "not_observed_again": 1,
        "inferred_closed": 0,
    }


    changed = {
        event["desertion_no"]: event
        for event in test_events
        if event["event_type"] == "CHANGED"
    }

    assert changed["B"]["status_group"] == "PROTECTING"
    assert changed["B"]["previous_state_raw"] == "종료(입양)"
    assert changed["C"]["closure_reason_code"] == "입양"
    assert len(test_events) == 7

    # 중복 공고번호가 있으면 계산을 멈춘다.
    try:
        transform_status_history(
            sample_previous,
            sample_current + [sample_current[0]],
            "2026-10-28",
            "2026-10-30",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Duplicate key must fail")

    print("History tests: PASS")
    print("Return-to-protecting test: PASS")
    print("Duplicate key test: PASS")

    for event in events:
        if event["event_type"] == "CHANGED":
            print("CHANGE SAMPLE:", event)
            break


if __name__ == "__main__":
    main()
