from types import SimpleNamespace
from unittest.mock import Mock, patch

import pyarrow as pa

from rebom.storage.silver.photo_version_history import apply_photo_version_history


def test_photo_history_upsert_is_batched():
    rows = [
        {"desertion_no": str(i), "image_slot": 1, "version": 1}
        for i in range(401)
    ]

    schema = pa.schema([
        ("desertion_no", pa.string()),
        ("image_slot", pa.int32()),
        ("version", pa.int32()),
    ])

    table = Mock()
    table.location.return_value = "s3://bucket/test"
    table.schema.return_value.as_arrow.return_value = schema
    table.scan.return_value.to_arrow.return_value.to_pylist.return_value = []

    batch_sizes = []

    def fake_upsert(arrow, **kwargs):
        batch_sizes.append(arrow.num_rows)
        assert kwargs["join_cols"] == [
            "desertion_no", "image_slot", "version"
        ]
        return SimpleNamespace(
            rows_inserted=arrow.num_rows,
            rows_updated=0,
        )

    table.upsert.side_effect = fake_upsert

    catalog = Mock()
    catalog.load_table.return_value = table

    plans = [
        {
            "insert_rows": rows,
            "update_rows": [],
            "insert_count": 401,
            "update_count": 0,
            "skip_count": 0,
        },
        {
            "insert_rows": [],
            "update_rows": [],
            "insert_count": 0,
            "update_count": 0,
            "skip_count": 401,
        },
    ]

    with patch(
        "rebom.storage.silver.photo_version_history.plan_photo_history_inserts",
        side_effect=plans,
    ):
        result = apply_photo_version_history(
            catalog,
            "rebom_silver.test",
            rows,
            "s3://bucket/test",
            apply=True,
        )

    assert batch_sizes == [200, 200, 1]
    assert result["rows_inserted"] == 401
    assert result["rows_updated"] == 0
    assert result["result"] == "APPLIED_AND_VERIFIED"

def test_photo_history_batch_recovers_after_partial_failure():
    import pytest

    rows = [
        {"desertion_no": str(i), "image_slot": 1, "version": 1}
        for i in range(401)
    ]

    schema = pa.schema([
        ("desertion_no", pa.string()),
        ("image_slot", pa.int32()),
        ("version", pa.int32()),
    ])

    stored = {}
    fail_once = [True]

    table = Mock()
    table.location.return_value = "s3://bucket/test"
    table.schema.return_value.as_arrow.return_value = schema
    table.scan.return_value.to_arrow.return_value.to_pylist.side_effect = (
        lambda: list(stored.values())
    )

    def fake_upsert(arrow, **kwargs):
        batch = arrow.to_pylist()

        if len(stored) == 200 and fail_once[0]:
            fail_once[0] = False
            raise RuntimeError("SIMULATED_BATCH_FAILURE")

        for row in batch:
            stored[(row["desertion_no"], row["image_slot"], row["version"])] = row

        return SimpleNamespace(
            rows_inserted=len(batch),
            rows_updated=0,
        )

    table.upsert.side_effect = fake_upsert

    catalog = Mock()
    catalog.load_table.return_value = table

    def fake_plan(incoming, existing):
        existing_keys = {
            (row["desertion_no"], row["image_slot"], row["version"])
            for row in existing
        }

        inserts = [
            row for row in incoming
            if (row["desertion_no"], row["image_slot"], row["version"])
            not in existing_keys
        ]

        return {
            "insert_rows": inserts,
            "update_rows": [],
            "insert_count": len(inserts),
            "update_count": 0,
            "skip_count": len(incoming) - len(inserts),
        }

    with patch(
        "rebom.storage.silver.photo_version_history.plan_photo_history_inserts",
        side_effect=fake_plan,
    ):
        with pytest.raises(RuntimeError, match="SIMULATED_BATCH_FAILURE"):
            apply_photo_version_history(
                catalog, "rebom_silver.test",
                rows, "s3://bucket/test", apply=True,
            )

        assert len(stored) == 200

        result = apply_photo_version_history(
            catalog, "rebom_silver.test",
            rows, "s3://bucket/test", apply=True,
        )

    assert len(stored) == 401
    assert result["rows_inserted"] == 201
    assert result["rows_updated"] == 0
    assert result["result"] == "APPLIED_AND_VERIFIED"
