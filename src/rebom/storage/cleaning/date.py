from datetime import datetime


def parse_date(value):
    # 원본이 없거나 빈 문자열이면 날짜를 생성하지 않는다.
    if value is None or not str(value).strip():
        return None

    text = str(value).strip()

    # 구조동물 API의 YYYYMMDD 형식만 허용한다.
    if len(text) != 8 or not text.isdigit():
        return None

    try:
        # 실제로 존재하지 않는 날짜도 검사한다.
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None
