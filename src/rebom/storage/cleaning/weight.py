import re


def parse_weight(weight):
    # 원본 체중이 없으면 missing으로 구분한다.
    if weight is None or not str(weight).strip():
        return None, None, "missing"

    value = str(weight).strip()

    # API에서 붙여주는 (Kg)와 중복된 kg 표기를 제거한다.
    value = re.sub(r"\(Kg\)$", "", value, flags=re.IGNORECASE).strip()
    value = re.sub(r"kg$", "", value, flags=re.IGNORECASE).strip()

    # -(Kg)는 측정값이 없는 경우로 처리한다.
    if value in ("", "-"):
        return None, None, "missing"

    # 쉼표로 입력된 소수점을 표준 소수점으로 변환한다.
    if re.fullmatch(r"\d+,\d+", value):
        value = value.replace(",", ".")

    # 단일 체중은 최소값과 최대값을 동일하게 저장한다.
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        number = float(value)
        return number, number, "parsed_single"

    # 범위형 체중은 최소값과 최대값으로 나누어 저장한다.
    if re.fullmatch(r"\d+(?:\.\d+)?~\d+(?:\.\d+)?", value):
        minimum, maximum = map(float, value.split("~"))

        if minimum <= maximum:
            return minimum, maximum, "parsed_range"

    # 의미를 확정할 수 없는 체중은 추측해서 변환하지 않는다.
    return None, None, "ambiguous"
