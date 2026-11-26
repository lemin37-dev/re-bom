import json
from pathlib import Path

from rebom.storage.silver.transform_shelter import (
    transform_shelter,
    transform_shelters,
)


def main():
    # 실제 API 샘플 200건으로 보호소 추출을 검증한다.
    records = json.loads(
        Path("data/sample/rescue_sample.json").read_text(encoding="utf-8")
    )

    shelters, conflicts, missing = transform_shelters(records)

    keys = [row["care_reg_no"] for row in shelters]

    # 보호소 식별자는 유일해야 한다.
    assert len(keys) == len(set(keys))

    # 동일 보호소가 여러 공고에 나오더라도 한 건만 생성한다.
    sample = [
        {
            "desertionNo": "A001",
            "careRegNo": "S001",
            "careNm": "테스트 보호소",
            "careTel": "02-1111-1111",
        },
        {
            "desertionNo": "A002",
            "careRegNo": "S001",
            "careNm": "테스트 보호소",
            "careAddr": "서울시 테스트구",
        },
        {
            "desertionNo": "A003",
            "careRegNo": "S001",
            "careNm": "다른 보호소 이름",
        },
    ]

    test_shelters, test_conflicts, test_missing = transform_shelters(sample)

    assert len(test_shelters) == 1
    assert test_shelters[0]["care_address"] == "서울시 테스트구"
    assert len(test_conflicts) == 1
    assert test_conflicts[0]["field"] == "care_name"
    assert not test_missing

    # 식별자 누락 사례를 확인한다.
    assert transform_shelter({"careNm": "이름 없는 보호소"}) is None

    print("Total notices:", len(records))
    print("Unique shelters:", len(shelters))
    print("Missing shelter keys:", len(missing))
    print("Shelter field conflicts:", len(conflicts))
    print("Unique key validation: PASS")
    print("Duplicate and conflict tests: PASS")

    for item in conflicts[:5]:
        print("CONFLICT:", item)

    for item in missing[:5]:
        print("MISSING KEY:", item)

    if shelters:
        print("First shelter:", shelters[0])


if __name__ == "__main__":
    main()
