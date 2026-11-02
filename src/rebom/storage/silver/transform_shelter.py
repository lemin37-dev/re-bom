SHELTER_FIELDS = {
    "careNm": "care_name",
    "careAddr": "care_address",
    "careTel": "care_tel",
    "orgNm": "org_name",
}


def transform_shelter(raw):
    # 보호소 식별자가 없으면 다른 보호소와 안전하게 구별할 수 없다.
    care_reg_no = raw.get("careRegNo")

    if care_reg_no is None or not str(care_reg_no).strip():
        return None

    result = {"care_reg_no": str(care_reg_no).strip()}

    # API 원본 필드명을 Silver 컬럼명으로 변환한다.
    for source, target in SHELTER_FIELDS.items():
        value = raw.get(source)
        result[target] = (
            str(value).strip()
            if value is not None and str(value).strip()
            else None
        )

    return result


def transform_shelters(records):
    # 동일 보호소는 care_reg_no를 키로 묶는다.
    shelters = {}
    conflicts = []
    missing_keys = []

    for raw in records:
        shelter = transform_shelter(raw)

        if shelter is None:
            missing_keys.append(raw.get("desertionNo"))
            continue

        key = shelter["care_reg_no"]

        if key not in shelters:
            shelters[key] = shelter
            continue

        existing = shelters[key]

        # 기존 값이 없으면 수집된 값을 보완한다.
        # 서로 다른 값이 있으면 임의로 덮어쓰지 않고 충돌로 기록한다.
        for field in SHELTER_FIELDS.values():
            old_value = existing[field]
            new_value = shelter[field]

            if old_value is None and new_value is not None:
                existing[field] = new_value
            elif (
                old_value is not None
                and new_value is not None
                and old_value != new_value
            ):
                conflicts.append({
                    "care_reg_no": key,
                    "desertion_no": raw.get("desertionNo"),
                    "field": field,
                    "existing_value": old_value,
                    "incoming_value": new_value,
                })

    return list(shelters.values()), conflicts, missing_keys
