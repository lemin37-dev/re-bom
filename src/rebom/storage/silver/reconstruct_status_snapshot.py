"""부분 Bronze 증분과 기존 Silver를 조합해 상태 비교용 스냅샷을 재구성한다."""

from datetime import datetime

from rebom.storage.silver.resolve_ingest_events import resolve_ingest_events
from rebom.storage.silver.validate_closure_evidence import validate_closure_evidence
from rebom.storage.silver.transform_status_history import transform_status_history


NOTICE_KINDS = ("updated", "list_notice", "list_protect")


def to_status_raw(row):
    # 기존 Silver 데이터와 신규 정제 결과를 동일한 비교 형식으로 만든다.
    # 상태 이력 생성에 필요한 최소 원본 필드만 복원한다.
    state = row.get("process_state_raw")

    # 보호중 목록에서는 원본 processState가 없을 수 있다.
    # 앞 단계에서 이미 보호중으로 정제된 경우에만 보정한다.
    if state is None and row.get("status_group") == "PROTECTING":
        state = "보호중"

    return {
        "desertionNo": str(row["desertion_no"]),
        "processState": state,
        "updTm": row.get("source_updated_at_raw"),
    }


def index_silver(rows):
    result = {}

    for row in rows:
        key = str(row.get("desertion_no") or "").strip()

        if not key or key in result:
            raise ValueError(f"Silver 공고번호 오류: {key}")

        result[key] = row

    return result


def reconstruct_status_snapshot(
    verified_run,
    previous_silver_rows,
    prepared_notice,
    previous_observed_on,
):
    # 입력은 다음 두 단계에서 검증된 결과를 사용해야 한다.
    #
    # 1. load_bronze_run(): 압축 파일 체크섬 및 건수 검증
    # 2. prepare_notice_delta(): 중복 및 내용 충돌 검증
    #
    # 여기서는 DB 쓰기를 수행하지 않는다.
    if not prepared_notice["ready"]:
        raise ValueError("충돌이 해결되지 않은 Silver 증분입니다.")

    previous = index_silver(previous_silver_rows)

    # A팀 manifest의 실제 조회 구간과 lookup 결과를 먼저 확인한다.
    # 종료 근거가 하나라도 부족하면 상태 재구성 및 이력 생성을 차단한다.
    closure = validate_closure_evidence(
        verified_run,
        previous_silver_rows,
    )

    if not closure["ready"]:
        return {
            "ready": False,
            "conflicts": closure["rejected"],
            "history_rows": [],
            "closure_evidence": closure,
        }

    delta = index_silver(prepared_notice["delta_rows"])

    # 원래 Silver 전체 공고를 기준으로 시작한다.
    # 변경분에 없는 공고를 삭제하지 않는다.
    current = dict(previous)
    current.update(delta)

    # 실제 이번 실행에서 관측된 공고번호를 별도로 수집한다.
    #
    # 주의:
    # prepare_notice_delta()는 변경 없는 공고를 delta에서 제외한다.
    # 따라서 delta만 보면 실제로 관측된 공고를 놓칠 수 있다.
    observed_ids = set()

    for kind in NOTICE_KINDS:
        for raw in verified_run["rows_by_kind"].get(kind, []):
            key = str(raw.get("desertionNo") or "").strip()

            if not key:
                raise ValueError(f"{kind}: 공고번호 누락")

            observed_ids.add(key)

    events = verified_run["rows_by_kind"].get("events", [])
    reprocess_ids = set(verified_run.get("reprocess_ids") or [])

    # 사라짐 이벤트가 있고 이번 실행에서 관측되지 않은 공고만
    # 상태 비교용 현재 스냅샷에서 제외한다.
    #
    # 확인되지 않은 단순 미관측 공고는 계속 유지한다.
    # 이 제외 처리는 메모리 안에서만 수행하며 실제 Silver 행은 삭제하지 않는다.
    for event in events:
        if event.get("event") != "vanished":
            continue

        key = str(event.get("desertionNo") or "").strip()

        if key in reprocess_ids:
            raise ValueError(f"재확인 대상의 종료 처리 금지: {key}")

        if key not in observed_ids:
            current.pop(key, None)

    previous_raw = [
        to_status_raw(row) for row in previous.values()
    ]
    current_raw = [
        to_status_raw(row) for row in current.values()
    ]

    # 이미 확인한 이벤트 분류 함수를 다시 사용한다.
    resolved = resolve_ingest_events(
        previous_raw,
        current_raw,
        events,
    )

    if resolved["conflicts"]:
        return {
            "ready": False,
            "conflicts": resolved["conflicts"],
            "history_rows": [],
        }

    fetched_at = verified_run["fetched_at"]
    current_day = datetime.fromisoformat(fetched_at).date().isoformat()

    # 기존 상태 이력 함수를 재사용한다.
    history, stats = transform_status_history(
        previous_records=previous_raw,
        current_records=current_raw,
        previous_observed_on=previous_observed_on,
        current_observed_on=current_day,
        confirmed_missing_ids=resolved["confirmed_missing_ids"],
    )

    # 기존 함수는 이전 공고 전체에 INITIAL을 다시 만들어 준다.
    # 증분 적재에서는 기존 INITIAL을 반복 저장하면 안 된다.
    #
    # 기존에 없던 신규 공고의 INITIAL과 실제 CHANGED만 남긴다.
    changes = [
        row for row in history
        if not (
            row["event_type"] == "INITIAL"
            and row["desertion_no"] in previous
        )
    ]

    inferred_ids = set(resolved["confirmed_missing_ids"])
    evidence = resolved["confirmation_evidence"]

    enriched = []

    for row in changes:
        item = dict(row)
        key = item["desertion_no"]

        item["run_id"] = verified_run["run_id"]
        item["fetched_at"] = fetched_at
        item["confirmed_by"] = (
            evidence.get(key)
            if item["event_type"] == "CHANGED"
            else None
        )
        item["is_inferred"] = (
            key in inferred_ids
            and item["event_type"] == "CHANGED"
            and item["process_state_raw"] == "종료(사유 미상)"
        )

        enriched.append(item)

    return {
        "ready": True,
        "conflicts": [],
        "history_rows": enriched,
        "resolved": resolved,
        "statistics": stats,
        "previous_count": len(previous),
        "current_count": len(current),
        "observed_count": len(observed_ids),
    }
