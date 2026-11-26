"""B Silver regression: a new observed animal with a returned event stays INITIAL."""

from copy import deepcopy
from pathlib import Path
from runpy import run_path

from rebom.storage.silver.reconstruct_status_snapshot import reconstruct_status_snapshot

existing_test = Path(__file__).with_name("rebom_test_reconstruct_status_snapshot.py")
if not existing_test.is_file():
    raise FileNotFoundError(f"EXISTING_TEST_NOT_FOUND: {existing_test}")

fixture = run_path(str(existing_test))
verified = deepcopy(fixture["verified"])
previous_silver = fixture["previous_silver"]
prepared = fixture["prepared"]

verified["rows_by_kind"]["events"].append({
    "event": "returned",
    "desertionNo": "E",
})

result = reconstruct_status_snapshot(
    verified,
    previous_silver,
    prepared,
    previous_observed_on="2026-11-02",
)

assert result["ready"], result.get("conflicts")
assert result["resolved"]["returned_ids"] == ["C"]
assert result["previous_count"] == 4
assert result["current_count"] == 4
assert result["observed_count"] == 3

new_animal_history = [
    row for row in result["history_rows"] if row["desertion_no"] == "E"
]
assert len(new_animal_history) == 1, new_animal_history
assert new_animal_history[0]["event_type"] == "INITIAL", new_animal_history
assert len(result["history_rows"]) == 4

print("NEW_OBSERVED_WITH_RETURNED: PASS")
print("NEW_ANIMAL_INITIAL_ONLY: PASS")
print("EXISTING_RETURNED_PRESERVED: PASS")
print("HISTORY_NO_DUPLICATES: PASS")
print("ICEBERG_WRITE: NOT_PERFORMED")
print("RESULT: PASS")
