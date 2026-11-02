from rebom.storage.cleaning.age import parse_age
from rebom.storage.cleaning.breed import parse_breed
from rebom.storage.cleaning.color import parse_color
from rebom.storage.cleaning.date import parse_date
from rebom.storage.cleaning.status import parse_status
from rebom.storage.cleaning.weight import parse_weight


def transform_notice(raw):
    # 공고번호는 필수 식별자다.
    desertion_no = raw.get("desertionNo")

    if not desertion_no:
        raise ValueError("desertionNo is required")

    # 역할별 정제 함수를 호출한다.
    birth_year, age_note = parse_age(raw.get("age"))
    weight_min, weight_max, weight_status = parse_weight(raw.get("weight"))
    status_group, closure_reason = parse_status(raw.get("processState"))
    color_normalized, color_status = parse_color(raw.get("colorCd"))
    breed = parse_breed(raw)

    result = {
        "desertion_no": str(desertion_no),
        "age_raw": raw.get("age"),
        "birth_year": birth_year,
        "age_note": age_note,
        "weight_raw": raw.get("weight"),
        "weight_min_kg": weight_min,
        "weight_max_kg": weight_max,
        "weight_parse_status": weight_status,
        "process_state_raw": raw.get("processState"),
        "status_group": status_group,
        "closure_reason_code": closure_reason,
        "happen_date": parse_date(raw.get("happenDt")),
        "notice_start_date": parse_date(raw.get("noticeSdt")),
        "notice_end_date": parse_date(raw.get("noticeEdt")),
        "color_raw": raw.get("colorCd"),
        "color_normalized": color_normalized,
        "color_parse_status": color_status,
    }

    # 품종 관련 컬럼을 공고 결과에 합친다.
    result.update(breed)

    return result
