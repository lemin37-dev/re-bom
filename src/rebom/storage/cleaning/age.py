import re


def parse_age(age):
    if age is None or not str(age).strip():
        return None, None

    value = str(age).strip()
    match = re.match(r"^(\d{4})(.*)$", value)

    if not match:
        return None, None

    birth_year = int(match.group(1))
    remainder = match.group(2)

    notes = re.findall(r"\(([^()]*)\)", remainder)
    notes = [note for note in notes if note != "년생"]

    age_note = ", ".join(notes) if notes else None

    return birth_year, age_note
