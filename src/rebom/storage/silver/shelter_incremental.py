"""??? ?? ?? ??? ??."""

from rebom.storage.silver.transform_shelter import (
    SHELTER_FIELDS,
    transform_shelters,
)


def prepare_shelter_delta(raw_rows, previous_rows):
    previous = {}

    for row in previous_rows:
        key = row["care_reg_no"]

        if not key or key in previous:
            raise ValueError("Invalid or duplicate shelter key")

        previous[key] = dict(row)

    incoming, conflicts, missing = transform_shelters(raw_rows)

    blocked = {
        (item["care_reg_no"], item["field"])
        for item in conflicts
    }

    inserts = []
    updates = []
    unchanged = []
    changes = []

    for shelter in incoming:
        key = shelter["care_reg_no"]
        old = previous.get(key)

        if old is None:
            candidate = dict(shelter)

            for field in SHELTER_FIELDS.values():
                if (key, field) in blocked:
                    candidate[field] = None

            inserts.append(candidate)
            continue

        candidate = dict(old)

        for field in SHELTER_FIELDS.values():
            value = shelter[field]

            if value is None or (key, field) in blocked:
                continue

            if old.get(field) != value:
                candidate[field] = value

                changes.append({
                    "care_reg_no": key,
                    "field": field,
                    "previous_value": old.get(field),
                    "new_value": value,
                })

        if candidate != old:
            updates.append(candidate)
        else:
            unchanged.append(key)

    observed = {
        row["care_reg_no"] for row in incoming
    }

    return {
        "insert_rows": inserts,
        "update_rows": updates,
        "delta_rows": inserts + updates,
        "unchanged_keys": unchanged,
        "not_observed_keys": sorted(set(previous) - observed),
        "changes": changes,
        "conflicts": conflicts,
        "missing_keys": missing,
    }
