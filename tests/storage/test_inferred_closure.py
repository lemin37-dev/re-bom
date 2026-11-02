from rebom.storage.silver.transform_status_history import (
    transform_status_history,
)


def test_inferred_closure():
    # 이전에는 두 공고 모두 보호중이었다.
    previous = [
        {"desertionNo": "A001", "processState": "\uBCF4\uD638\uC911"},
        {"desertionNo": "A002", "processState": "\uBCF4\uD638\uC911"},
    ]

    # 현재 조회에서는 두 공고 모두 발견되지 않았다.
    current = []

    # A001만 수집 검증을 통해 종료 판정이 확인되었다고 가정한다.
    events, stats = transform_status_history(
        previous,
        current,
        "2026-11-01",
        "2026-11-02",
        confirmed_missing_ids=["A001"],
    )

    # 현재 관측일에 생성된 이벤트만 확인한다.
    closed_events = [
        event
        for event in events
        if event["observed_on"] == "2026-11-02"
    ]

    # 종료가 확인된 A001만 종료 이력이 생성되어야 한다.
    assert len(closed_events) == 1
    assert closed_events[0]["desertion_no"] == "A001"
    assert closed_events[0]["status_group"] == "CLOSED"
    assert closed_events[0]["closure_reason_code"] == "\uC0AC\uC720 \uBBF8\uC0C1"

    # A002는 조회에서 빠졌지만 종료 처리하지 않는다.
    assert stats["not_observed_again"] == 2
    assert stats["inferred_closed"] == 1


if __name__ == "__main__":
    test_inferred_closure()
    print("Inferred closure test: PASS")
