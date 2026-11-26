"""Reconcile run journal against verified storage rows."""

from rebom.storage.silver.run_journal import read_run, write_run, STAGES


def reconcile_run(run_id, directory, notice_verified, history_verified):
    """Advance journal only after storage verification.

    The booleans must come from reading and checking the storage tables,
    not from successful return values of write operations.
    """
    if type(notice_verified) is not bool:
        raise TypeError("notice_verified must be bool")
    if type(history_verified) is not bool:
        raise TypeError("history_verified must be bool")

    if history_verified and not notice_verified:
        raise ValueError("History exists but notice verification failed")

    record = read_run(run_id, directory)

    if record is None:
        record = write_run(run_id, directory, "PREPARED")

    observed_stage = (
        "COMPLETED"
        if notice_verified and history_verified
        else "NOTICE_APPLIED"
        if notice_verified
        else "PREPARED"
    )

    recorded_index = STAGES.index(record["stage"])
    observed_index = STAGES.index(observed_stage)

    if recorded_index > observed_index:
        raise ValueError(
            "Journal is ahead of verified storage; manual investigation required"
        )

    # Advance in order; never skip a persisted stage.
    for stage in STAGES[recorded_index + 1:observed_index + 1]:
        record = write_run(run_id, directory, stage)

    return {
        "run_id": run_id,
        "stage": record["stage"],
        "observed_stage": observed_stage,
        "reconciled": True,
    }
