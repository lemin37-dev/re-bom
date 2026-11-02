import json
from pathlib import Path

from rebom.storage.silver.transform_status_history import (
    transform_status_history,
)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main():
    root = Path("data/sample/incremental")

    previous = load_json(root / "t0_2026-10-28.json")
    current = load_json(root / "truth_2026-10-30.json")
    expected = load_json(root / "expected.json")

    events, stats = transform_status_history(
        previous,
        current,
        "2026-10-28",
        "2026-10-30",
    )

    # A팀이 정의한 시나리오를 상태 변경, 신규, 유지로 묶는다.
    changed_categories = [
        "복귀(종료→보호중)",
        "상태 변경, 수정일 조회가 돌려줌",
        "상태 변경, 수정일 조회가 놓침",
    ]

    new_categories = [
        "신규, 수정일 조회가 돌려줌(보호중)",
        "신규, 처음부터 종료",
        "신규, 수정일 조회에 없음",
    ]

    unchanged_categories = [
        "사진·비고 칸만 변경(updTm 그대로)",
        "변경 없음(보호중)",
        "변경 없음(종료)",
        "변경 없음(기타 축종)",
    ]

    expected_categories = (
        changed_categories + new_categories + unchanged_categories
    )

    assert set(expected) == set(expected_categories)

    # 서로 다른 시나리오에 동일 공고번호가 중복 포함되지 않았는지 검증한다.
    all_ids = [
        str(item)
        for category in expected_categories
        for item in expected[category]
    ]

    assert len(all_ids) == len(set(all_ids)), (
        "Duplicate IDs across expected scenarios"
    )

    # 실제 상태 변경 이벤트의 공고번호 집합을 만든다.
    actual_changed = {
        event["desertion_no"]
        for event in events
        if event["event_type"] == "CHANGED"
    }

    previous_ids = {str(row["desertionNo"]) for row in previous}
    current_ids = {str(row["desertionNo"]) for row in current}

    actual_new = current_ids - previous_ids
    actual_unchanged = (
        previous_ids & current_ids
    ) - actual_changed

    def expected_ids(categories):
        return {
            str(item)
            for category in categories
            for item in expected[category]
        }

    expected_changed = expected_ids(changed_categories)
    expected_new = expected_ids(new_categories)
    expected_unchanged = expected_ids(unchanged_categories)

    groups = [
        ("CHANGED", expected_changed, actual_changed),
        ("NEW", expected_new, actual_new),
        ("UNCHANGED", expected_unchanged, actual_unchanged),
    ]

    failures = []

    for name, expected_set, actual_set in groups:
        missing = sorted(expected_set - actual_set)
        unexpected = sorted(actual_set - expected_set)

        passed = not missing and not unexpected

        print(
            f"{name}: expected={len(expected_set)}, "
            f"actual={len(actual_set)}, "
            f"result={'PASS' if passed else 'FAIL'}"
        )

        if not passed:
            failures.append(name)
            print("  Missing:", missing[:10])
            print("  Unexpected:", unexpected[:10])

    # 세 그룹이 전체 현재 스냅샷을 빠짐없이 설명해야 한다.
    assert (
        expected_changed | expected_new | expected_unchanged
    ) == current_ids

    # 복귀 시나리오는 반드시 종료 상태에서 보호중으로 바뀌어야 한다.
    changed_events = {
        event["desertion_no"]: event
        for event in events
        if event["event_type"] == "CHANGED"
    }

    for notice_id in expected["복귀(종료→보호중)"]:
        event = changed_events.get(str(notice_id))

        assert event is not None, notice_id
        assert str(event["previous_state_raw"]).startswith("종료(")
        assert event["status_group"] == "PROTECTING"

    print("Return-to-protecting scenarios: PASS")

    if failures:
        raise AssertionError(f"Expected scenario mismatch: {failures}")

    print("Expected scenario validation: PASS")
    print("Total expected notices:", len(all_ids))
    print("Total history events:", len(events))


if __name__ == "__main__":
    main()
