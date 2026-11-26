import json
from collections import Counter
from datetime import date
from pathlib import Path

from rebom.storage.cleaning.date import parse_date
from rebom.storage.cleaning.status import parse_status
from rebom.storage.silver.transform_notice import transform_notice


def main():
    # A팀 샘플 200건을 공고 정제 함수에 넣는다.
    records = json.loads(
        Path("data/sample/rescue_sample.json").read_text(encoding="utf-8")
    )

    results = []
    failures = []

    for raw in records:
        try:
            results.append(transform_notice(raw))
        except (ValueError, TypeError, KeyError) as error:
            failures.append((raw.get("desertionNo"), str(error)))

    weight_counts = Counter(
        item["weight_parse_status"] for item in results
    )
    status_counts = Counter(
        item["status_group"] for item in results
    )
    date_failures = [
        item["desertion_no"]
        for item in results
        if any(
            item[field] is None
            for field in (
                "happen_date",
                "notice_start_date",
                "notice_end_date",
            )
        )
    ]

    print("Total:", len(records))
    print("Success:", len(results))
    print("Failed:", len(failures))
    print("Weight status:", dict(weight_counts))
    print("Status group:", dict(status_counts))
    print("Date failures:", len(date_failures))

    # 상태 코드가 올바르게 변환되는지 검증한다.
    assert parse_status("보호중") == ("PROTECTING", None)
    assert parse_status("종료(입양)") == ("CLOSED", "입양")
    assert parse_status("종료(안락사)") == ("CLOSED", "안락사")
    assert parse_status("예상하지못한값") == ("UNKNOWN", None)

    # 기존 날짜 정제 검증도 유지한다.
    assert parse_date("20261020") == date(2026, 10, 20)
    assert parse_date("20260230") is None

    assert len(records) == 200
    assert len(results) == 200
    assert not failures
    assert not date_failures
    assert status_counts["UNKNOWN"] == 0


if __name__ == "__main__":
    main()
