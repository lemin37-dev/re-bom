from rebom.storage.cleaning.status import parse_status


def index_notices(records):
    # 공고번호를 기준으로 조회하기 위한 딕셔너리를 만든다.
    indexed = {}

    for raw in records:
        key = str(raw.get("desertionNo") or "").strip()

        if not key:
            raise ValueError("desertionNo is required")

        if key in indexed:
            raise ValueError(f"Duplicate desertionNo: {key}")

        indexed[key] = raw

    return indexed


def make_status_event(
    desertion_no,
    raw,
    observed_on,
    event_type,
    previous_state=None,
):
    # 상태는 원본과 표준값을 모두 저장한다.
    current_state = raw.get("processState")
    status_group, closure_reason = parse_status(current_state)

    return {
        "desertion_no": desertion_no,
        "observed_on": observed_on,
        "event_type": event_type,
        "previous_state_raw": previous_state,
        "process_state_raw": current_state,
        "status_group": status_group,
        "closure_reason_code": closure_reason,
        "source_updated_at_raw": raw.get("updTm"),
    }


def transform_status_history(
    previous_records,
    current_records,
    previous_observed_on,
    current_observed_on,
):
    # 각 시점의 공고번호별 데이터를 만든다.
    previous = index_notices(previous_records)
    current = index_notices(current_records)

    events = []
    statistics = {
        "initial": 0,
        "changed": 0,
        "unchanged": 0,
        "new": 0,
        "not_observed_again": 0,
    }

    # 이전 스냅샷의 상태를 최초 관측 이력으로 저장한다.
    for key in sorted(previous):
        events.append(
            make_status_event(
                key,
                previous[key],
                previous_observed_on,
                "INITIAL",
            )
        )
        statistics["initial"] += 1

    # 현재 스냅샷과 비교해 변경 또는 신규 공고를 찾는다.
    for key in sorted(current):
        raw = current[key]

        if key not in previous:
            events.append(
                make_status_event(
                    key,
                    raw,
                    current_observed_on,
                    "INITIAL",
                )
            )
            statistics["new"] += 1
            continue

        old_state = previous[key].get("processState")
        new_state = raw.get("processState")

        # 원본 상태값이 같으면 상태 이력을 추가하지 않는다.
        if old_state == new_state:
            statistics["unchanged"] += 1
            continue

        events.append(
            make_status_event(
                key,
                raw,
                current_observed_on,
                "CHANGED",
                previous_state=old_state,
            )
        )
        statistics["changed"] += 1

    # 다음 조회에서 보이지 않는 공고를 종료 처리하지 않는다.
    statistics["not_observed_again"] = len(
        previous.keys() - current.keys()
    )

    return events, statistics
