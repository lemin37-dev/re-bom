from rebom.storage.silver.validate_manifest import validate_partition


def test_partition_validation():
    # A팀 검증 조건에 따른 정상 구간
    valid, reason = validate_partition(
        "00", "2023-01-01", "2025-12-30", 4796, 4796
    )
    assert valid is True
    assert reason == "complete"

    # 실제 관측된 불완전 수집 사례를 재현한다.
    valid, reason = validate_partition(
        "00", "2023-01-01", "2025-12-30", 4796, 4793
    )
    assert valid is False
    assert reason == "count_mismatch"

    # 결과 코드가 비정상이면 건수가 같아도 실패한다.
    valid, reason = validate_partition(
        "99", "2026-01-01", "2026-01-02", 10, 10
    )
    assert valid is False
    assert reason == "api_result_error"

    # 허용 기간 초과는 실패한다.
    valid, reason = validate_partition(
        "00", "2020-01-01", "2023-01-01", 10, 10
    )
    assert valid is False
    assert reason == "invalid_date_range"

    # 조회 결과가 0건이어도 완전한 구간이면 정상이다.
    valid, reason = validate_partition(
        "00", "2010-01-01", "2010-12-31", 0, 0
    )
    assert valid is True
    assert reason == "complete"

    # 잘못된 수신 건수는 실패한다.
    valid, reason = validate_partition(
        "00", "2026-01-01", "2026-01-02", 10, -1
    )
    assert valid is False
    assert reason == "invalid_count"


if __name__ == "__main__":
    test_partition_validation()
    print("Partition validation tests: PASS")
