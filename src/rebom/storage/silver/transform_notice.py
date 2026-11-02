from rebom.storage.cleaning.age import parse_age
from rebom.storage.cleaning.breed import parse_breed
from rebom.storage.cleaning.color import parse_color
from rebom.storage.cleaning.date import parse_date
from rebom.storage.cleaning.status import parse_status
from rebom.storage.cleaning.weight import parse_weight


def transform_notice(raw):
    # 공고번호는 Silver에서 공고 한 건을 식별하는 필수 키다.
    desertion_no = raw.get("desertionNo")

    if not desertion_no or not str(desertion_no).strip():
        raise ValueError("desertionNo is required")

    # 필드별 정제 함수는 별도 모듈에서 수행한다.
    birth_year, age_note = parse_age(raw.get("age"))
    weight_min, weight_max, weight_status = parse_weight(raw.get("weight"))
    status_group, closure_reason = parse_status(raw.get("processState"))
    color_normalized, color_status = parse_color(raw.get("colorCd"))
    breed = parse_breed(raw)

    result = {
        "desertion_no": str(desertion_no),
        "notice_no": raw.get("noticeNo"),
        "care_reg_no": raw.get("careRegNo"),
        "special_mark": raw.get("specialMark"),
        "happen_place": raw.get("happenPlace"),
        "org_name": raw.get("orgNm"),
        "sex_code": raw.get("sexCd"),
        "neuter_code": raw.get("neuterYn"),
        "end_reason_text": raw.get("endReason"),

        # updTm의 시간대가 아직 확인되지 않아 원문으로 보존한다.
        "source_updated_at_raw": raw.get("updTm"),

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

    # 품종 관련 컬럼을 공고 결과에 병합한다.
    result.update(breed)

    return result
