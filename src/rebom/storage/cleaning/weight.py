import re


def parse_weight(weight):
    if weight is None or not str(weight).strip():
        return None, None, "missing"

    value = str(weight).strip()

    value = re.sub(r"\(Kg\)$", "", value, flags=re.IGNORECASE).strip()
    value = re.sub(r"kg$", "", value, flags=re.IGNORECASE).strip()

    if re.fullmatch(r"\d+,\d+", value):
        value = value.replace(",", ".")

    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        number = float(value)
        return number, number, "parsed_single"

    if re.fullmatch(r"\d+(?:\.\d+)?~\d+(?:\.\d+)?", value):
        minimum, maximum = map(float, value.split("~"))

        if minimum <= maximum:
            return minimum, maximum, "parsed_range"

    return None, None, "ambiguous"
