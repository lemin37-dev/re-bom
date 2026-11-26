import json
from collections import Counter
from pathlib import Path

from rebom.storage.silver.transform_notice import transform_notice
from rebom.storage.silver.transform_image import transform_images
from rebom.storage.silver.transform_shelter import transform_shelters
from rebom.storage.silver.transform_status_history import transform_status_history


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main():
    # 공고 200건을 네 가지 Silver 검증의 기본 데이터로 사용한다.
    raw_notices = load_json("data/sample/rescue_sample.json")

    notices = [transform_notice(raw) for raw in raw_notices]

    images = []
    for raw in raw_notices:
        images.extend(transform_images(raw))

    shelters, conflicts, missing_shelter_keys = transform_shelters(
        raw_notices
    )

    # 공고번호는 Silver 공고 테이블에서 유일해야 한다.
    notice_ids = [row["desertion_no"] for row in notices]
    assert len(notice_ids) == len(set(notice_ids))
    notice_id_set = set(notice_ids)

    # 이미지의 (공고번호, 이미지 슬롯)은 중복되면 안 된다.
    image_keys = [
        (row["desertion_no"], row["image_slot"])
        for row in images
    ]
    assert len(image_keys) == len(set(image_keys))

    orphan_images = [
        row for row in images
        if row["desertion_no"] not in notice_id_set
    ]

    # 보호소 식별자는 유일해야 한다.
    shelter_ids = [row["care_reg_no"] for row in shelters]
    assert len(shelter_ids) == len(set(shelter_ids))
    shelter_id_set = set(shelter_ids)

    # 공고가 참조하는 보호소가 추출 결과에 있는지 검사한다.
    orphan_shelter_refs = [
        row["desertion_no"]
        for row in notices
        if row["care_reg_no"] not in shelter_id_set
    ]

    # 상태 이력은 별도 증분 샘플을 사용한다.
    root = Path("data/sample/incremental")

    previous = load_json(root / "t0_2026-10-28.json")
    current = load_json(root / "truth_2026-10-30.json")

    history, history_stats = transform_status_history(
        previous,
        current,
        "2026-10-28",
        "2026-10-30",
    )

    # 동일 공고, 동일 관측일에는 같은 이벤트가 중복되면 안 된다.
    history_keys = [
        (
            row["desertion_no"],
            row["observed_on"],
            row["event_type"],
        )
        for row in history
    ]
    assert len(history_keys) == len(set(history_keys))

    # 샘플별 생성 건수와 연결 관계를 검증한다.
    assert len(notices) == 200
    assert len(images) == 524
    assert len(shelters) == 119
    assert not missing_shelter_keys
    assert not orphan_images
    assert not orphan_shelter_refs

    assert len(history) == 159
    assert history_stats["changed"] == 44
    assert history_stats["new"] == 28
    assert history_stats["unchanged"] == 43

    print("=== Silver Integration ===")
    print("Notices:", len(notices))
    print("Images:", len(images))
    print("Shelters:", len(shelters))
    print("Shelter conflicts:", len(conflicts))
    print("History events:", len(history))
    print("History types:", dict(Counter(
        row["event_type"] for row in history
    )))

    print("Notice unique key: PASS")
    print("Image unique key: PASS")
    print("Image foreign key: PASS")
    print("Shelter unique key: PASS")
    print("Shelter reference: PASS")
    print("History event key: PASS")
    print("Silver integration tests: PASS")


if __name__ == "__main__":
    main()
