import re


CLOSED_REASONS = {
    "입양",
    "반환",
    "자연사",
    "안락사",
    "기증",
    "방사",
    "사유 미상",
}


def parse_status(value):
    # 원본 상태가 없으면 UNKNOWN으로 분류한다.
    if value is None or not str(value).strip():
        return "UNKNOWN", None

    text = str(value).strip()

    # 보호중은 서비스 검색 대상이 되는 현재 상태다.
    if text == "보호중":
        return "PROTECTING", None

    # 종료 공고는 종료 유형을 따로 추출한다.
    match = re.fullmatch(r"종료\(([^()]+)\)", text)

    if match:
        reason = match.group(1)

        if reason in CLOSED_REASONS:
            return "CLOSED", reason

    # 예상하지 못한 상태를 임의로 종료 처리하지 않는다.
    return "UNKNOWN", None
