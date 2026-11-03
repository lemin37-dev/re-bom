"""A팀 vanished 이벤트의 종료 근거를 수집 구간과 실제 조회 결과로 검증한다."""

from datetime import date, datetime

from rebom.storage.cleaning.status import parse_status
from rebom.storage.silver.validate_manifest import validate_partition


def normalize_day(value):
    # 기존 Silver의 date 객체, API의 YYYYMMDD 문자열을 모두 처리한다.
    # 날짜를 모르는 공고는 구간 기반 종료로 확정할 수 없다.
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if isinstance(value, str):
        try:
            if len(value) == 8 and value.isdigit():
                return datetime.strptime(value, "%Y%m%d").date()

            return date.fromisoformat(value)
        except ValueError:
            return None

    return None


def validate_closure_evidence(verified_run, previous_silver_rows):
    # verified_run은 load_bronze_run()으로 검증한 입력을 사용한다.
    # 이 함수는 종료 처리 가능 여부만 판단하고 데이터를 수정하지 않는다.
    #
    # 성공: 모든 vanished 이벤트에 확인 가능한 근거가 있음
    # 실패: 하나라도 근거가 부족하면 전체 실행의 종료 처리를 차단

    partitions = verified_run.get("partitions")
    rows_by_kind = verified_run.get("rows_by_kind") or {}

    if not isinstance(partitions, list):
        raise ValueError("manifest partitions가 전달되지 않았습니다.")

    previous = {}

    for row in previous_silver_rows:
        key = str(row.get("desertion_no") or "").strip()

        if not key or key in previous:
            raise ValueError(f"기존 Silver 공고번호 오류: {key}")

        previous[key] = row

    lookup = {}

    for raw in rows_by_kind.get("lookup", []):
        key = str(raw.get("desertionNo") or "").strip()

        if not key:
            raise ValueError("lookup 공고번호 누락")

        lookup.setdefault(key, []).append(raw)

    reprocess = set(verified_run.get("reprocess_ids") or [])

    for partition in partitions:
        reprocess.update(partition.get("reprocess_ids") or [])

    validated = []
    rejected = []
    seen = set()

    for event in rows_by_kind.get("events", []):
        if not isinstance(event, dict):
            raise ValueError("이벤트 형식 오류")

        if event.get("event") != "vanished":
            continue

        key = str(event.get("desertionNo") or "").strip()
        method = event.get("confirmed_by")

        if not key or key in seen:
            raise ValueError("종료 이벤트 공고번호 누락 또는 중복")

        seen.add(key)

        reason = None
        old = previous.get(key)

        if old is None:
            reason = "previous_not_found"

        elif key in reprocess:
            reason = "reprocess_pending"

        elif old.get("status_group") != "PROTECTING":
            reason = "previous_not_protecting"

        else:
            old_day = normalize_day(old.get("happen_date"))
            event_day = normalize_day(event.get("happenDt"))

            if old_day is None or event_day is None:
                reason = "happen_date_missing"

            elif old_day != event_day:
                reason = "happen_date_mismatch"

            elif method == "complete_range":
                # 두 종류의 목록 모두 발생일을 포함하는 완전한 구간이 필요하다.
                for kind in ("list_notice", "list_protect"):
                    covering = [
                        p for p in partitions
                        if p.get("kind") == kind
                        and normalize_day(p.get("start_date")) is not None
                        and normalize_day(p.get("end_date")) is not None
                        and normalize_day(p["start_date"]) <= old_day
                        <= normalize_day(p["end_date"])
                    ]

                    # 누락 또는 중복된 구간은 종료 근거로 사용하지 않는다.
                    if len(covering) != 1:
                        reason = f"{kind}_coverage_invalid"
                        break

                    p = covering[0]

                    complete, expected_reason = validate_partition(
                        p.get("result_code"),
                        p.get("start_date"),
                        p.get("end_date"),
                        p.get("total_count"),
                        p.get("received_count"),
                    )

                    if (
                        not complete
                        or p.get("complete") is not True
                        or p.get("reason") != expected_reason
                    ):
                        reason = f"{kind}_incomplete"
                        break

            elif method == "lookup":
                # lookup이 종료 확인 근거라면 실제 조회 레코드가 있어야 한다.
                # event 안의 종료 상태 문구만 믿고 처리하지 않는다.
                matches = [
                    raw for raw in lookup.get(key, [])
                    if normalize_day(raw.get("happenDt")) == old_day
                    and parse_status(raw.get("processState"))[0] == "CLOSED"
                ]

                if not matches:
                    reason = "lookup_closed_record_missing"

            else:
                reason = "invalid_confirmation"

        if reason:
            rejected.append({
                "desertion_no": key,
                "reason": reason,
            })
        else:
            validated.append({
                "desertion_no": key,
                "confirmed_by": method,
            })

    return {
        "ready": not rejected,
        "validated": validated,
        "rejected": rejected,
    }
