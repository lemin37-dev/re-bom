from rebom.storage.cleaning.color import parse_color_tags


def test_color_tags():
    assert parse_color_tags("\uac80\uc815&\ud770\uc0c9") == (
        ["\uac80\uc815\uacc4\uc5f4", "\ud770\uc0c9\uacc4\uc5f4"], None
    )
    assert parse_color_tags("\ud06c\ub9bc\uc0c9") == (
        ["\ud770\uc0c9\uacc4\uc5f4"], None
    )
    assert parse_color_tags("\ud669\uac08\uc0c9") == (
        ["\uac08\uc0c9\uacc4\uc5f4"], None
    )
    assert parse_color_tags("\ud638\ud53c") == (
        ["\ub178\ub791\uacc4\uc5f4"], "\ud638\ud53c"
    )
    assert parse_color_tags("\uc0bc\uc0c9", "\uac1c") == (
        ["\uae30\ud0c0"], "\uc0bc\uc0c9"
    )
    assert parse_color_tags(None) == (None, None)
