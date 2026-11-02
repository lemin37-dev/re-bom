import json
from pathlib import Path

from rebom.storage.silver.transform_notice import transform_notice


def main():
    # A팀 샘플 200건을 읽는다.
    records = json.loads(
        Path("data/sample/rescue_sample.json").read_text(encoding="utf-8")
    )

    fields = {
        "noticeNo": "notice_no",
        "careRegNo": "care_reg_no",
        "specialMark": "special_mark",
        "happenPlace": "happen_place",
        "orgNm": "org_name",
        "sexCd": "sex_code",
        "neuterYn": "neuter_code",
        "endReason": "end_reason_text",
        "updTm": "source_updated_at_raw",
    }

    for raw in records:
        result = transform_notice(raw)

        # 원본 필드가 Silver 컬럼에 정확히 전달되는지 확인한다.
        for source_field, target_field in fields.items():
            assert result[target_field] == raw.get(source_field), (
                raw.get("desertionNo"),
                source_field,
                target_field,
            )

        assert result["desertion_no"] == raw["desertionNo"]

    # 필수 키가 없으면 정상 공고로 처리하지 않는다.
    try:
        transform_notice({"age": "2026(년생)"})
    except ValueError:
        pass
    else:
        raise AssertionError("Missing desertionNo must fail")

    print("Total:", len(records))
    print("Notice field mapping: PASS")
    print("Missing key validation: PASS")
    print("First record keys:", len(transform_notice(records[0])))


if __name__ == "__main__":
    main()
