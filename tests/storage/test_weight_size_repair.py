import pytest

from scripts.storage.rebom_repair_weight_size import plan_repair


@pytest.mark.parametrize(
    "weight, size, expected_count",
    [
        (9.9, "small", 0),
        (10.0, "small", 1),
        (10.0, "medium", 0),
        (10.1, "medium", 0),
        (24.9, "medium", 0),
        (25.0, "large", 0),
        (None, None, 0),
    ],
)
def test_repair_target(weight, size, expected_count):
    row = {
        "desertion_no": "TEST001",
        "weight_max_kg": weight,
        "size": size,
        "status_group": "PROTECTING",
    }

    original = dict(row)
    changes = plan_repair([row])

    assert len(changes) == expected_count
    assert row == original

    if changes:
        assert changes[0] == {**original, "size": "medium"}


def test_repair_idempotency():
    row = {
        "desertion_no": "TEST002",
        "weight_max_kg": 10.0,
        "size": "small",
        "status_group": "CLOSED",
    }

    first = plan_repair([row])
    second = plan_repair(first)

    assert len(first) == 1
    assert second == []
    assert first[0]["status_group"] == "CLOSED"
