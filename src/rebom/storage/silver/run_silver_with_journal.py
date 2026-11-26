"""Coordinate test-table Silver writes with a durable run journal."""

from rebom.storage.silver.apply_notice_pipeline import get_local_catalog
from rebom.storage.silver.apply_silver_pipeline import (
    NOTICE_TABLE,
    HISTORY_TABLE,
    NOTICE_KEYS,
    HISTORY_KEYS,
    apply_prepared_silver,
    index_rows,
    prepare_arrow,
)
from rebom.storage.silver.reconcile_run_journal import reconcile_run


def verify_expected(catalog, table_name, expected_rows, keys):
    expected = index_rows(expected_rows, keys)
    stored = index_rows(
        catalog.load_table(table_name).scan().to_arrow().to_pylist(),
        keys,
    )
    return all(stored.get(key) == row for key, row in expected.items())


def run_silver_with_journal(
    prepared,
    journal_directory,
    stop_after_notice=False,
):
    if not prepared.get("ready"):
        raise ValueError("Silver preparation is not ready")

    run_id = prepared["run_id"]
    catalog = get_local_catalog()

    notice = catalog.load_table(NOTICE_TABLE)
    history = catalog.load_table(HISTORY_TABLE)

    notice_rows = prepare_arrow(
        prepared["notice_delta_rows"],
        notice,
        NOTICE_KEYS,
        require_all_fields=True,
    ).to_pylist()

    history_rows = prepare_arrow(
        prepared["history_rows"],
        history,
        HISTORY_KEYS,
    ).to_pylist()

    for row in history_rows:
        if row["run_id"] != run_id:
            raise ValueError("History run_id mismatch")

    def reconcile():
        notice_verified = verify_expected(
            catalog, NOTICE_TABLE, notice_rows, NOTICE_KEYS
        )
        history_verified = verify_expected(
            catalog, HISTORY_TABLE, history_rows, HISTORY_KEYS
        )
        return reconcile_run(
            run_id,
            journal_directory,
            notice_verified,
            history_verified,
        )

    before = reconcile()

    if before["stage"] == "COMPLETED":
        return {"run_id": run_id, "stage": "COMPLETED", "reused": True}

    written = apply_prepared_silver(
        prepared,
        apply=True,
        stop_after_notice=stop_after_notice,
    )

    after = reconcile()

    return {
        "run_id": run_id,
        "stage": after["stage"],
        "reused": False,
        "write_result": written,
    }
