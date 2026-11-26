from datetime import datetime


def validate_partition(
    result_code,
    start_date,
    end_date,
    total_count,
    received_count,
):
    # API 결과 코드가 정상인지 확인한다.
    # 00이 아니면 종료 판정의 근거로 사용할 수 없다.
    if str(result_code) != "00":
        return False, "api_result_error"

    # 조회 시작일과 종료일은 YYYY-MM-DD 형식이어야 한다.
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False, "invalid_date"

    # 조회 기간은 시작일과 종료일을 모두 포함한다.
    span_days = (end - start).days + 1

    if span_days < 1 or span_days > 1095:
        return False, "invalid_date_range"

    # 총건수와 실제 수신 건수는 음수가 아닌 정수여야 한다.
    if (
        type(total_count) is not int
        or type(received_count) is not int
        or total_count < 0
        or received_count < 0
    ):
        return False, "invalid_count"

    # API가 안내한 총건수와 실제 받은 건수가 일치해야 한다.
    if total_count != received_count:
        return False, "count_mismatch"

    # 모든 조건이 맞으면 종료 판정에 사용할 수 있는 구간이다.
    return True, "complete"
