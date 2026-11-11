"""Bronze 파일 검증부터 Silver 공고 증분 준비까지 연결한다."""

from pathlib import Path

from rebom.storage.silver.load_bronze import load_bronze_run
from rebom.storage.silver.prepare_notice_delta import prepare_notice_delta


def run_notice_pipeline(manifest_path, existing_silver_rows):
    # 1. A팀의 수집 파일을 검증한다.
    #
    # checksum, record_count, 필수 파일, 수집 구간 메타데이터를
    # 기존 load_bronze_run()에서 검증한다.
    #
    # 검증에 실패하면 예외가 발생하므로
    # 정제 단계까지 도달하지 않는다.
    verified_run = load_bronze_run(Path(manifest_path))

    # 2. 검증된 Bronze 데이터만 증분 준비 모듈에 전달한다.
    #
    # 여러 파일에 같은 desertionNo가 존재할 수 있으므로
    # 먼저 중복·내용 충돌을 확인한다.
    #
    # 이전 Silver에 있지만 이번 증분에서 보이지 않는 공고는
    # 이 단계에서 삭제하거나 종료 처리하지 않는다.
    prepared = prepare_notice_delta(
        verified_run,
        existing_silver_rows,
    )

    # 3. 충돌이 발생하면 적재 가능 상태로 승인하지 않는다.
    #
    # 출처 간 우선순위를 아직 확정하지 않았으므로
    # 임의로 updated 또는 list_protect를 선택하지 않는다.
    if not prepared["ready"]:
        return {
            "ready": False,
            "run_id": verified_run["run_id"],
            "delta_rows": [],
            "insert_count": 0,
            "update_count": 0,
            "conflicts": prepared["conflicts"],
            "incomplete_partitions": verified_run["incomplete_partitions"],
            "statistics": {
                "total_input": prepared["total_input"],
                "unique_count": prepared["unique_count"],
                "duplicate_count": prepared["duplicate_count"],
            },
        }

    # 4. 충돌이 없는 실행만 후속 Iceberg 적재 대상으로 반환한다.
    #
    # 이 함수에서는 Iceberg를 변경하지 않는다.
    # 실제 쓰기 기능을 붙일 때에는 별도 승인·재실행 검사를 둔다.
    return {
        "ready": True,
        "run_id": verified_run["run_id"],
        "delta_rows": prepared["delta_rows"],
        "insert_count": prepared["insert_count"],
        "update_count": prepared["update_count"],
        "unchanged_count": prepared["unchanged_count"],
        "conflicts": [],
        "incomplete_partitions": verified_run["incomplete_partitions"],
        "statistics": {
            "total_input": prepared["total_input"],
            "unique_count": prepared["unique_count"],
            "duplicate_count": prepared["duplicate_count"],
        },
    }
