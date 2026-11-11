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


_COLOR_ALIASES = {
    "\uac80\uc815\uc0c9": "\uac80\uc815\uacc4\uc5f4",
    "\uac80\uc740\uc0c9": "\uac80\uc815\uacc4\uc5f4",
    "\uac80\uc815": "\uac80\uc815\uacc4\uc5f4",
    "\ud751\uc0c9": "\uac80\uc815\uacc4\uc5f4",
    "\ud558\uc580\uc0c9": "\ud770\uc0c9\uacc4\uc5f4",
    "\ud770\uc0c9": "\ud770\uc0c9\uacc4\uc5f4",
    "\ud770": "\ud770\uc0c9\uacc4\uc5f4",
    "\ud06c\ub9bc": "\ud770\uc0c9\uacc4\uc5f4",
    "\ubbf8\uc0c9": "\ud770\uc0c9\uacc4\uc5f4",
    "\uc544\uc774\ubcf4\ub9ac": "\ud770\uc0c9\uacc4\uc5f4",
    "\ud669\uac08\uc0c9": "\uac08\uc0c9\uacc4\uc5f4",
    "\uace0\ub3d9": "\uac08\uc0c9\uacc4\uc5f4",
    "\ubca0\uc774\uc9c0": "\uac08\uc0c9\uacc4\uc5f4",
    "\uac08\uc0c9": "\uac08\uc0c9\uacc4\uc5f4",
    "\ub178\ub791": "\ub178\ub791\uacc4\uc5f4",
    "\ub178\ub780": "\ub178\ub791\uacc4\uc5f4",
    "\uae08\uc0c9": "\ub178\ub791\uacc4\uc5f4",
    "\uc0b4\uad6c": "\ub178\ub791\uacc4\uc5f4",
    "\uaca8\uc790": "\ub178\ub791\uacc4\uc5f4",
    "\ub808\ubaac": "\ub178\ub791\uacc4\uc5f4",
    "\uce58\uc988": "\ub178\ub791\uacc4\uc5f4",
    "\uc778\uc808\ubbf8": "\ub178\ub791\uacc4\uc5f4",
    "\ud68c\uc0c9": "\ud68c\uc0c9\uacc4\uc5f4",
    "\uc7bf\ube5b": "\ud68c\uc0c9\uacc4\uc5f4",
    "\ud638\ud53c": "\ub178\ub791\uacc4\uc5f4",
    "\ud638\ubc18": "\ub178\ub791\uacc4\uc5f4",
    "\ud638\uad6c": "\ub178\ub791\uacc4\uc5f4",
    "\ube0c\ub9b0\ub4e4": "\ub178\ub791\uacc4\uc5f4",
}

_COLOR_PATTERNS = {
    "\ud638\ud53c": ("\ud638\ud53c", "\ud638\ubc18", "\ud638\uad6c", "\ube0c\ub9b0\ub4e4"),
    "\uc5bc\ub8e9": ("\uc5bc\ub8e9",),
    "\uc0bc\uc0c9": ("\uc0bc\uc0c9",),
    "\ubc18\uc810": ("\ubc18\uc810",),
    "\ud131\uc2dc\ub3c4": ("\ud131\uc2dc\ub3c4",),
    "\uc904\ubb34\ub2ac": ("\uc904\ubb34\ub2ac",),
}


def parse_color_tags(value, species_name=None):
    if value is None or not str(value).strip():
        return None, None

    text = str(value).strip()
    matches = []
    occupied = set()

    for alias, tag in sorted(
        _COLOR_ALIASES.items(),
        key=lambda pair: len(pair[0]),
        reverse=True,
    ):
        start = 0
        while True:
            pos = text.find(alias, start)
            if pos < 0:
                break
            span = set(range(pos, pos + len(alias)))
            if not occupied.intersection(span):
                matches.append((pos, tag))
                occupied.update(span)
            start = pos + len(alias)

    tags = list(dict.fromkeys(tag for _, tag in sorted(matches)))
    patterns = [
        (text.find(alias), pattern)
        for pattern, aliases in _COLOR_PATTERNS.items()
        for alias in aliases
        if alias in text
    ]
    pattern = min(patterns)[1] if patterns else None

    species = str(species_name or "").strip().strip("[]")
    if species == "\uace0\uc591\uc774" and "\uc0bc\uc0c9" in text:
        for tag in (
            "\ud770\uc0c9\uacc4\uc5f4",
            "\uac80\uc815\uacc4\uc5f4",
            "\ub178\ub791\uacc4\uc5f4",
        ):
            if tag not in tags:
                tags.append(tag)

    return tags or ["\uae30\ud0c0"], pattern
