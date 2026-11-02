from rebom.storage.cleaning.age import parse_age
from rebom.storage.cleaning.weight import parse_weight


def transform_notice(raw):
    desertion_no = raw.get("desertionNo")

    if not desertion_no:
        raise ValueError("desertionNo is required")

    birth_year, age_note = parse_age(raw.get("age"))
    weight_min, weight_max, weight_status = parse_weight(raw.get("weight"))

    return {
        "desertion_no": str(desertion_no),
        "age_raw": raw.get("age"),
        "birth_year": birth_year,
        "age_note": age_note,
        "weight_raw": raw.get("weight"),
        "weight_min_kg": weight_min,
        "weight_max_kg": weight_max,
        "weight_parse_status": weight_status,
        "process_state_raw": raw.get("processState"),
    }
