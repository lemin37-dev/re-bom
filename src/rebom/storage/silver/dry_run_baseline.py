"""Restore verified previous-day notice states for DRY_RUN comparisons."""

from rebom.storage.cleaning.status import parse_status
from rebom.storage.silver.reconstruct_status_snapshot import index_silver
from rebom.storage.silver.transform_notice import transform_notice
from rebom.storage.silver.validate_closure_evidence import normalize_day


NOTICE_KINDS = ("updated", "list_notice", "list_protect")
STATE_FIELDS = (
    "status_group", "process_state_raw", "closure_reason_code",
    "end_reason_text", "source_updated_at_raw",
)


def build_dry_run_baseline(previous_silver_rows, previous_bronze, events):
    existing = index_silver(previous_silver_rows)
    targets = {}
    for event in events:
        if event.get("event") != "vanished" or event.get("confirmed_by") not in ("complete_range", "lookup"):
            continue
        key = str(event.get("desertionNo") or "")
        if not key or key != key.strip() or key in targets:
            raise ValueError(f"BASELINE_EVENT_ID_CONFLICT: {key}")
        targets[key] = event

    targets = {
        key: event for key, event in targets.items()
        if key not in existing or existing[key].get("status_group") != "PROTECTING"
    }
    candidates = {}
    for kind in NOTICE_KINDS:
        for raw in previous_bronze["rows_by_kind"].get(kind, []):
            raw_key = str(raw.get("desertionNo") or "")
            key = raw_key.strip()
            if key not in targets:
                continue
            if raw_key != key:
                raise ValueError(f"BASELINE_NOTICE_ID_MISMATCH: {key}")

            day = normalize_day(raw.get("happenDt"))
            state = parse_status(raw.get("processState"))
            previous = candidates.get(key)
            if previous is not None:
                if (day, state) != previous[:2]:
                    raise ValueError(f"BASELINE_CONFLICT: {key}")
                continue

            candidates[key] = (day, state, kind, raw)

    missing = targets.keys() - candidates.keys()
    if missing:
        raise ValueError(f"DRY_RUN_PREVIOUS_BRONZE_MISSING: {sorted(missing)}")

    baseline = dict(existing)
    restored = []
    added = 0
    for key, event in sorted(targets.items()):
        day, (group, _), kind, raw = candidates[key]
        old = existing.get(key)
        if old is not None and str(old["desertion_no"]) != key:
            raise ValueError(f"BASELINE_NOTICE_ID_MISMATCH: {key}")
        if day is None or day != normalize_day(event.get("happenDt")) or (
            old is not None and day != normalize_day(old.get("happen_date"))
        ):
            raise ValueError(f"BASELINE_HAPPEN_DATE_MISMATCH: {key}")
        if group != "PROTECTING":
            raise ValueError(f"BASELINE_NOT_PROTECTING: {key}")

        row = transform_notice(raw, source_type=kind)
        if old is None:
            baseline[key] = row
            added += 1
        else:
            baseline[key] = {**old, **{field: row[field] for field in STATE_FIELDS}}
            restored.append((key, old.get("status_group"), kind))

    for key, old_group, kind in restored:
        print(
            f"DRY_RUN_BASELINE_RESTORED: desertion_no={key} "
            f"silver_status={old_group} bronze_status=PROTECTING "
            f"bronze_run_id={previous_bronze['run_id']} source={kind} "
            "reason=confirmed_vanished_with_previous_day_protection"
        )
    print("DRY_RUN_BASELINE_ADDED:", added)
    return list(baseline.values())
