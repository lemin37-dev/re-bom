
from rebom.storage.cleaning.status import parse_status


def index_notices(records):
    # 공고번호를 기준으로 빠르게 조회하기 위한 딕셔너리를 만든다.
    indexed = {}

    for raw in records:
        key = str(raw.get("desertionNo") or "").strip()

        # 공고번호가 없으면 동일 공고를 식별할 수 없다.
        if not key:
            raise ValueError("desertionNo is required")

        # 동일 스냅샷에 같은 공고번호가 두 번 있으면 중단한다.
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
    # API 원본 상태와 Silver 표준 상태를 함께 보존한다.
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
    confirmed_missing_ids=None,
):
    # 이전 및 현재 스냅샷을 공고번호 기준으로 정리한다.
    previous = index_notices(previous_records)
    current = index_notices(current_records)

    # A팀 수집 검증을 거쳐 종료 판정이 확인된 공고번호만 받는다.
    # None이면 종료 확인된 공고가 없는 것으로 처리한다.
    confirmed_missing_ids = set(
        confirmed_missing_ids
        if confirmed_missing_ids is not None
        else []
    )

    # 이전에는 있었지만 현재 목록에 없는 공고를 찾는다.
    # 목록에서 사라진 사실만으로 종료라고 판단하지 않는다.
    missing_ids = previous.keys() - current.keys()

    # 종료 확인 목록에 실제로 사라지지 않은 공고가 있으면 오류다.
    if not confirmed_missing_ids.issubset(missing_ids):
        raise ValueError(
            "confirmed_missing_ids must contain only missing previous notices"
        )

    # 종료 판정 대상은 이전 상태가 보호중이어야 한다.
    # 이벤트를 만들기 전에 전체 대상을 검사한다.
    for key in confirmed_missing_ids:
        old_state = previous[key].get("processState")
        old_group, _ = parse_status(old_state)

        if old_group != "PROTECTING":
            raise ValueError(
                f"Only PROTECTING notices can be inferred closed: {key}"
            )

    events = []

    statistics = {
        "initial": 0,
        "changed": 0,
        "unchanged": 0,
        "new": 0,
        "not_observed_again": 0,
        "inferred_closed": 0,
    }

    # 이전 스냅샷에 존재하는 공고를 최초 관측 이력으로 기록한다.
    # 기존 테스트와의 호환성을 위해 초기 이력 생성 방식을 유지한다.
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

    # 현재 스냅샷의 공고를 이전 데이터와 비교한다.
    for key in sorted(current):
        raw = current[key]

        # 이전에 없던 공고는 신규 관측 이력으로 기록한다.
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

        # 상태가 동일하면 불필요한 변경 이력을 만들지 않는다.
        if old_state == new_state:
            statistics["unchanged"] += 1
            continue

        # 상태가 바뀌면 변경 이력을 생성한다.
        # 종료에서 보호중으로 복귀하는 경우도 여기서 처리한다.
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

    # 이전에는 있었지만 현재 조회되지 않은 전체 공고 수를 기록한다.
    statistics["not_observed_again"] = len(missing_ids)

    # A팀의 수집 완전성 검증 또는 개별 조회를 통해
    # 종료 판정이 확인된 공고만 추정 종료 이력을 만든다.
    for key in sorted(confirmed_missing_ids):
        previous_raw = previous[key]
        old_state = previous_raw.get("processState")

        # 실제 API에서 종료 사유를 받은 것이 아니므로
        # 데이터 계약에 따라 '종료(사유 미상)'으로 기록한다.
        synthetic_raw = {
            "processState": "종료(사유 미상)",
            "updTm": None,
        }

        events.append(
            make_status_event(
                key,
                synthetic_raw,
                current_observed_on,
                "CHANGED",
                previous_state=old_state,
            )
        )

        statistics["changed"] += 1
        statistics["inferred_closed"] += 1

    return events, statistics
