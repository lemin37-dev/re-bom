import re


_SMALL_ANIMALS = {"개", "고양이"}


def parse_weight(weight, species_name=None):
    """Return (minimum kg, maximum kg, parsing status).

    Values with unclear units are not inferred. The 100 kg cap applies only
    to dogs and cats; other species keep their existing numeric parsing.
    """
    if weight is None or not str(weight).strip():
        return None, None, "missing"

    value = str(weight).strip()
    value = re.sub(r"\(Kg\)$", "", value, flags=re.IGNORECASE).strip()
    value = re.sub(r"kg$", "", value, flags=re.IGNORECASE).strip()

    if value in ("", "-"):
        return None, None, "missing"

    gram_match = re.fullmatch(
        r"(\d+(?:[.,]\d+)?)\s*g", value, flags=re.IGNORECASE
    )
    if gram_match:
        number = float(gram_match.group(1).replace(",", ".")) / 1000
        return number, number, "parsed_grams"

    if re.search(r"[gG]", value):
        return None, None, "ambiguous_unit"

    if re.fullmatch(r"\d+,\d+", value):
        value = value.replace(",", ".")

    if re.fullmatch(r"\d+\.\.\d+", value):
        value = value.replace("..", ".", 1)

    species = str(species_name or "").strip().strip("[]")

    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        number = float(value)
        if species in _SMALL_ANIMALS and number > 100:
            return None, None, "over_100kg_unverified"
        return number, number, "parsed_single"

    if re.fullmatch(r"\d+(?:\.\d+)?~\d+(?:\.\d+)?", value):
        minimum, maximum = map(float, value.split("~"))
        if minimum <= maximum:
            if species in _SMALL_ANIMALS and maximum > 100:
                return None, None, "over_100kg_unverified"
            return minimum, maximum, "parsed_range"

    return None, None, "ambiguous"
