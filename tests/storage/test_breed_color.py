import json
from collections import Counter
from pathlib import Path

from rebom.storage.cleaning.color import parse_color
from rebom.storage.silver.transform_notice import transform_notice


def main():
    # 실제 샘플 200건을 대상으로 검사한다.
    records = json.loads(
        Path("data/sample/rescue_sample.json").read_text(encoding="utf-8")
    )

    results = [transform_notice(raw) for raw in records]

    color_counts = Counter(
        row["color_parse_status"] for row in results
    )

    print("Total:", len(results))
    print("Color status:", dict(color_counts))

    # 기존 공고번호와 품종 원본이 유지되는지 검사한다.
    for raw, result in zip(records, results):
        assert result["desertion_no"] == raw["desertionNo"]
        assert result["breed_code"] == raw.get("kindCd")
        assert result["breed_full_name_raw"] == raw.get("kindFullNm")
        assert result["color_raw"] == raw.get("colorCd")

    # 명확한 동의어와 모호한 표현의 처리 결과를 검사한다.
    assert parse_color("검정색") == ("검정", "normalized")
    assert parse_color("흰색") == ("흰색", "normalized")
    assert parse_color("검정&흰색") == (None, "needs_review")
    assert parse_color(None) == (None, "missing")

    assert len(results) == 200

    print("Breed/color tests passed")


if __name__ == "__main__":
    main()
