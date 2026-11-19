from rebom.storage.cleaning.status import parse_status
from rebom.storage.silver.transform_notice import transform_notice


NOTICE_KINDS = ("updated", "list_notice", "list_protect")


def build_dry_run_baseline(previous_silver_rows, previous_bronze, events):
    existing = {
        str(row["desertion_no"]): row
        for row in previous_silver_rows
    }

    targets = {
        str(event["desertionNo"])
        for event in events
        if event.get("event") == "vanished"
        and event.get("confirmed_by") in ("complete_range", "lookup")
        and str(event.get("desertionNo") or "") not in existing
    }

    candidates = {}

    for kind in NOTICE_KINDS:
        for raw in previous_bronze["rows_by_kind"].get(kind, []):
            key = str(raw.get("desertionNo") or "").strip()

            if key not in targets:
                continue

            if parse_status(raw.get("processState"))[0] != "PROTECTING":
                continue

            previous = candidates.get(key)

            if previous is not None:
                if str(previous[1].get("happenDt")) != str(raw.get("happenDt")):
                    raise ValueError(f"BASELINE_CONFLICT: {key}")
                continue

            candidates[key] = (kind, raw)

    missing = targets - set(candidates)

    if missing:
        raise ValueError(
            f"DRY_RUN_PREVIOUS_BRONZE_MISSING: {sorted(missing)}"
        )

    additional = []

    for key in sorted(targets):
        kind, raw = candidates[key]
        row = transform_notice(raw, source_type=kind)

        if row["status_group"] != "PROTECTING":
            raise ValueError(f"BASELINE_NOT_PROTECTING: {key}")

        additional.append(row)

    print("DRY_RUN_BASELINE_ADDED:", len(additional))

    return list(previous_silver_rows) + additional
