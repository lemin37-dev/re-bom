"""A팀 수집 이벤트를 B팀 상태 이력 입력으로 안전하게 분류한다."""

from rebom.storage.cleaning.status import parse_status


def index_notices(records):
    # 이전/현재 공고를 desertionNo로 조회하기 쉽게 정리한다.
    # 같은 스냅샷에 중복된 공고가 있으면 임의 선택하지 않는다.
    result = {}

    for raw in records:
        key = str(raw.get("desertionNo") or "").strip()

        if not key or key in result:
            raise ValueError(f"Missing or duplicate desertionNo: {key}")

        result[key] = raw

    return result


def resolve_ingest_events(previous_records, current_records, events):
    # 이전과 현재는 비교하려는 동일 범위의 스냅샷이어야 한다.
    # 부분 증분 파일만 current_records로 전달하면 안 된다.
    previous = index_notices(previous_records)
    current = index_notices(current_records)

    confirmed_missing_ids = set()
    actual_closed_ids = set()
    returned_ids = set()
    conflicts = []
    evidence = {}
    seen = set()

    for event in events:
        if not isinstance(event, dict):
            raise ValueError("이벤트는 JSON 객체여야 합니다.")

        kind = event.get("event")
        key = str(event.get("desertionNo") or "").strip()

        if kind not in ("vanished", "returned") or not key:
            raise ValueError("잘못된 이벤트 종류 또는 공고번호")

        # 한 실행에서 같은 유형의 이벤트가 중복되면 한 번만 처리한다.
        # 이 dedup은 실행 내부에 한정된다. 실행 간 중복 방지는 후속 작업이다.
        event_key = (kind, key)

        if event_key in seen:
            continue

        seen.add(event_key)

        # A팀이 이전에 알고 있던 공고가 아니라면 이력 비교가 불가능하다.
        if key not in previous:
            conflicts.append((key, kind, "previous_not_found"))
            continue

        old_group, _ = parse_status(previous[key].get("processState"))
        new_group = None

        if key in current:
            new_group, _ = parse_status(current[key].get("processState"))

        if kind == "vanished":
            confirmed_by = event.get("confirmed_by")

            # 확인 근거가 없는 사라짐 이벤트는 종료로 인정하지 않는다.
            if confirmed_by not in ("complete_range", "lookup"):
                conflicts.append((key, kind, "invalid_confirmation"))
                continue

            if old_group != "PROTECTING":
                conflicts.append((key, kind, "previous_not_protecting"))
                continue

            if key not in current:
                # 실제 현재 공고가 없는 경우만 confirmed_missing_ids에 넣는다.
                # 기존 transform_status_history의 입력 조건을 만족한다.
                confirmed_missing_ids.add(key)
                evidence[key] = confirmed_by

            elif new_group == "CLOSED":
                # 이번 실행에서 실제 종료 상태를 확인했다.
                # 추정 종료를 만들지 않고 기존 일반 상태 변경을 사용한다.
                actual_closed_ids.add(key)
                evidence[key] = confirmed_by

            else:
                # 현재에도 보호중이거나 상태를 알 수 없으면 종료하지 않는다.
                conflicts.append((key, kind, "current_state_conflict"))

        elif kind == "returned":
            if old_group == "CLOSED" and new_group == "PROTECTING":
                # 기존 이력 함수가 CLOSED -> PROTECTING 변경을 기록한다.
                returned_ids.add(key)
            else:
                # 이전 상태가 종료가 아니거나 현재 보호중이 아니라면
                # 자동 복귀를 확정하지 않는다.
                conflicts.append((key, kind, "return_state_conflict"))

    return {
        "confirmed_missing_ids": sorted(confirmed_missing_ids),
        "actual_closed_ids": sorted(actual_closed_ids),
        "returned_ids": sorted(returned_ids),
        "conflicts": conflicts,
        "confirmation_evidence": evidence,
    }
