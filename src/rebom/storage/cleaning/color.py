SINGLE_COLORS = {
    "검정": "검정",
    "검정색": "검정",
    "검은색": "검정",
    "흰색": "흰색",
    "하얀색": "흰색",
    "갈색": "갈색",
    "회색": "회색",
    "크림색": "크림색",
}


def parse_color(value):
    # 원본값이 없다면 missing으로 구분한다.
    if value is None or not str(value).strip():
        return None, "missing"

    text = str(value).strip()

    # 명확한 단일 색상만 표준화한다.
    if text in SINGLE_COLORS:
        return SINGLE_COLORS[text], "normalized"

    # 복합 색상이나 모호한 표현은 임의로 해석하지 않는다.
    return None, "needs_review"
