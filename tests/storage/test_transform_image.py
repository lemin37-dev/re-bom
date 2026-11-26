import json
from collections import Counter
from pathlib import Path

from rebom.storage.silver.transform_image import transform_images


def main():
    # A팀이 제공한 실제 구조동물 샘플 200건을 읽는다.
    records = json.loads(
        Path("data/sample/rescue_sample.json").read_text(encoding="utf-8")
    )

    images = []

    for raw in records:
        images.extend(transform_images(raw))

    notice_ids = {str(raw["desertionNo"]) for raw in records}

    # 이미지가 존재하는 공고번호를 참조하는지 검증한다.
    assert all(row["desertion_no"] in notice_ids for row in images)

    # 한 공고의 동일 슬롯이 중복 생성되지 않아야 한다.
    keys = [
        (row["desertion_no"], row["image_slot"])
        for row in images
    ]
    assert len(keys) == len(set(keys))

    # 원본의 이미지 URL이 누락되거나 변경되지 않았는지 검증한다.
    for raw in records:
        actual = transform_images(raw)

        expected = [
            (slot, raw[f"popfile{slot}"].strip())
            for slot in range(1, 9)
            if isinstance(raw.get(f"popfile{slot}"), str)
            and raw[f"popfile{slot}"].strip()
        ]

        assert [
            (row["image_slot"], row["source_url"])
            for row in actual
        ] == expected

    # 사진 3장, 중간 슬롯 누락 사례를 별도로 검사한다.
    sample = {
        "desertionNo": "TEST001",
        "popfile1": "dog1.jpg",
        "popfile3": "dog3.jpg",
        "popfile8": "dog8.jpg",
    }
    result = transform_images(sample)

    assert len(result) == 3
    assert [row["image_slot"] for row in result] == [1, 3, 8]

    # 필수 공고번호가 없는 경우 오류가 발생해야 한다.
    try:
        transform_images({"popfile1": "dog1.jpg"})
    except ValueError:
        pass
    else:
        raise AssertionError("Missing desertionNo must fail")

    counts = Counter(row["desertion_no"] for row in images)

    print("Total notices:", len(records))
    print("Total image records:", len(images))
    print("Notices with images:", len(counts))
    print("Maximum images per notice:", max(counts.values(), default=0))
    print("Image mapping tests: PASS")
    print("Image reference tests: PASS")
    print("First image:", images[0] if images else None)


if __name__ == "__main__":
    main()
