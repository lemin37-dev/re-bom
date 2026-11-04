"""검증된 Bronze 데이터와 Silver 이벤트를 상태 처리용 입력으로 결합한다."""


def combine_verified_run(bronze_run, silver_events):
    if bronze_run["run_id"] != silver_events["bronze_run_id"]:
        raise ValueError("Bronze/Silver 실행 ID가 일치하지 않습니다.")

    bronze_rows = bronze_run["rows_by_kind"]
    event_rows = silver_events["rows_by_kind"]

    if "events" in bronze_rows or "lookup" in bronze_rows:
        raise ValueError("Bronze에 이벤트 또는 조회 결과가 포함돼 있습니다.")

    if "events" not in event_rows:
        raise ValueError("Silver 이벤트가 없습니다.")

    result = dict(bronze_run)
    result["rows_by_kind"] = {
        **bronze_rows,
        "events": event_rows["events"],
        "lookup": event_rows.get("lookup", []),
    }

    result["reprocess_ids"] = silver_events["reprocess_ids"]
    result["lookup_deferred_ids"] = silver_events["lookup_deferred_ids"]
    result["event_conflicts"] = silver_events["conflicts"]

    return result
