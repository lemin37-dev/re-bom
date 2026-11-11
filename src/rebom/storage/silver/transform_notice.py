
from rebom.storage.cleaning.age import parse_age
from rebom.storage.cleaning.breed import parse_breed
from rebom.storage.cleaning.color import parse_color, parse_color_tags
from rebom.storage.cleaning.date import parse_date
from rebom.storage.cleaning.status import parse_status
from rebom.storage.cleaning.weight import parse_weight


def transform_notice(raw, source_type=None):
    desertion_no = raw.get("desertionNo")

    if not desertion_no or not str(desertion_no).strip():
        raise ValueError("desertionNo is required")

    birth_year, age_note = parse_age(raw.get("age"))
    weight_min, weight_max, weight_status = parse_weight(raw.get("weight"), raw.get("upKindNm"))
    status_group, closure_reason = parse_status(raw.get("processState"))
    color_normalized, color_status = parse_color(raw.get("colorCd"))
    breed = parse_breed(raw)

    quality_warning = None

    if (
        not raw.get("processState")
        and source_type == "list_protect"
    ):
        status_group = "PROTECTING"
        quality_warning = "missing_process_state"

    result = {
        "desertion_no": str(desertion_no),
        "notice_no": raw.get("noticeNo"),
        "care_reg_no": raw.get("careRegNo"),
        "special_mark": raw.get("specialMark"),
        "info_text": " ".join(
            str(raw[key]).strip()
            for key in ("sfeSoci", "sfeHealth", "specialMark")
            if raw.get(key) is not None and str(raw[key]).strip()
        ) or None,
        "rfid_yn": bool(str(raw.get("rfidCd") or "").strip()),
        "happen_place": raw.get("happenPlace"),
        "org_name": raw.get("orgNm"),
        "sex_code": raw.get("sexCd"),
        "neuter_code": raw.get("neuterYn"),
        "end_reason_text": raw.get("endReason"),
        "source_updated_at_raw": raw.get("updTm"),
        "age_raw": raw.get("age"),
        "birth_year": birth_year,
        "age_note": age_note,
        "weight_raw": raw.get("weight"),
        "weight_min_kg": weight_min,
        "weight_max_kg": weight_max,
        "weight_kg": weight_max,
        "size": (
            "small" if weight_max <= 10
            else "medium" if weight_max < 25
            else "large"
        ) if (
            weight_max is not None
            and (
                str(raw.get("upKindCd") or "").strip() == "417000"
                or str(raw.get("upKindNm") or "").strip().strip("[]") == "\uac1c"
            )
        ) else None,
        "weight_parse_status": weight_status,
        "process_state_raw": raw.get("processState"),
        "status_group": status_group,
        "quality_warning": quality_warning,
        "closure_reason_code": closure_reason,
        "happen_date": parse_date(raw.get("happenDt")),
        "notice_start_date": parse_date(raw.get("noticeSdt")),
        "notice_date": parse_date(raw.get("noticeSdt")),
        "notice_end_date": parse_date(raw.get("noticeEdt")),
        "color_raw": raw.get("colorCd"),
        "color_normalized": color_normalized,
        "color_parse_status": color_status,
        "color_tag": parse_color_tags(raw.get("colorCd"), raw.get("upKindNm"))[0],
        "color_pattern": parse_color_tags(raw.get("colorCd"), raw.get("upKindNm"))[1],
    }

    result.update(breed)

    breed_name = str(raw.get("kindNm") or "")
    breed_code = str(raw.get("kindCd") or "").strip()
    result["is_mix"] = breed_code in {"000114", "000219", "000223", "000115"} or "\uBBF9\uC2A4" in breed_name
    result["u60_at_notice"] = "60" in str(raw.get("age") or "") and "\uC77C\uBBF8\uB9CC" in str(raw.get("age") or "").replace(" ", "")

    return result
