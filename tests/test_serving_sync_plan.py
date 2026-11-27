"""동기화 계획(rebom.serving.sync.plan) — DB·S3 없이 규칙만 본다."""
from datetime import datetime, timedelta, timezone

import pytest

from rebom.serving.sync import plan

T0 = datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc)
SHA_A, SHA_B, SHA_C = "a" * 64, "b" * 64, "c" * 64


def silver(no="1001", status="PROTECTING", **kw):
    row = {"desertion_no": no, "notice_no": f"N-{no}", "status_group": status, "process_state_raw": "보호중",
           "species_code": "417000", "species_name": "개", "sex_code": "f", "neuter_code": "U", "weight_kg": 9.0,
           "size": "small", "color_tag": ("갈색계열",), "care_reg_no": " 344500 ", "special_mark": "온순"}
    row.update(kw)
    return row


SHELTER = {"care_reg_no": "344500", "care_name": "당진시동물보호소", "care_tel": "000-0000-0000", "care_address": "충남"}


def test_notice_row_maps_columns_and_shelter():
    r = plan.notice_row(silver(), SHELTER)
    assert r["care_reg_no"] == "344500" and r["care_name"] == "당진시동물보호소"
    assert r["sex_code"] == "F" and r["color_tag"] == ["갈색계열"] and r["weight_kg"] == 9.0
    assert set(r) == set(plan.NOTICE_COLUMNS) | {"content_hash"}
    assert len(r["content_hash"]) == 64
    assert "careOwnerNm" not in r and "care_owner_name" not in r


def test_notice_row_rejects_bad_codes():
    r = plan.notice_row(silver(sex_code="X", size="huge"), None)
    assert r["sex_code"] is None and r["size"] is None and r["care_name"] is None
    with pytest.raises(ValueError):
        plan.notice_row(silver(status="ENDED"), None)


def test_content_hash_changes_with_content_only():
    a = plan.notice_row(silver(), SHELTER)
    assert a["content_hash"] == plan.notice_row(silver(), SHELTER)["content_hash"]
    assert a["content_hash"] != plan.notice_row(silver(special_mark="겁 많음"), SHELTER)["content_hash"]


def test_new_notice_only_when_protecting():
    rows = [plan.notice_row(silver("1"), None), plan.notice_row(silver("2", status="CLOSED"), None)]
    p = plan.plan_notices(rows, {}, T0)
    assert [r["desertion_no"] for r in p.upserts] == ["1"]
    assert p.skipped_not_protecting == 1
    assert p.upserts[0]["closed_transition_at"] is None and p.upserts[0]["visible_until"] is None


def test_close_sets_transition_once_and_reopen_clears():
    open_row = plan.notice_row(silver("1"), None)
    closed_row = plan.notice_row(silver("1", status="CLOSED", process_state_raw="종료(입양)"), None)
    was_open = {"1": {"content_hash": open_row["content_hash"], "status_group": "PROTECTING",
                      "closed_transition_at": None, "visible_until": None}}
    p = plan.plan_notices([closed_row], was_open, T0)
    assert p.closed_now == 1
    assert p.upserts[0]["closed_transition_at"] == T0
    assert p.upserts[0]["visible_until"] == T0 + timedelta(days=30)

    later = T0 + timedelta(days=3)
    was_closed = {"1": {"content_hash": closed_row["content_hash"], "status_group": "CLOSED",
                        "closed_transition_at": T0, "visible_until": T0 + timedelta(days=30)}}
    again = plan.plan_notices([closed_row], was_closed, later)
    assert again.upserts == [] and again.unchanged == 1  # 같은 종료를 다시 반영해도 30일을 늘리지 않는다

    back = plan.plan_notices([open_row], was_closed, later)
    assert back.reopened == 1
    assert back.upserts[0]["closed_transition_at"] is None and back.upserts[0]["visible_until"] is None


def test_unchanged_notice_skipped_and_naive_time_rejected():
    r = plan.notice_row(silver("1"), None)
    old = {"1": {"content_hash": r["content_hash"], "status_group": "PROTECTING",
                 "closed_transition_at": None, "visible_until": None}}
    assert plan.plan_notices([r], old, T0).unchanged == 1
    with pytest.raises(ValueError):
        plan.plan_notices([r], old, datetime(2026, 11, 24))
    with pytest.raises(ValueError):
        plan.plan_notices([r, r], {}, T0)


def photo(no, slot, sha, version=1, serving=True):
    suffix = f"_v{version:03d}" if version > 1 else ""
    return {"desertion_no": no, "image_slot": slot, "sha256": sha,
            "original_s3_key": f"bronze/rescue/images/{no}/popfile{slot}{suffix}.jpg",
            "serving_s3_key": f"images/{no}/{slot}{suffix}.webp" if serving else None,
            "thumb_s3_key": f"images/{no}/{slot}{suffix}_thumb.webp"}


def test_current_photos_skips_empty_and_reads_version():
    photos, present = plan.current_photos([photo("1", 1, SHA_A, 2), photo("1", 2, None), photo("2", 1, SHA_B, serving=False)])
    assert list(photos) == [("1", 1)] and present == {"1", "2"}
    assert photos[("1", 1)]["photo_version"] == 2
    with pytest.raises(ValueError):
        plan.current_photos([photo("1", 1, "XYZ")])
    with pytest.raises(ValueError):
        plan.current_photos([photo("1", 1, SHA_A), photo("1", 1, SHA_A)])


def test_plan_photos_upserts_deletes_and_keeps_absent_notices():
    photos, present = plan.current_photos([photo("1", 1, SHA_A), photo("1", 2, None), photo("3", 1, SHA_C)])
    existing = {("1", 1): {"sha256": SHA_B, "photo_version": 1, "serving_s3_key": "images/1/1.webp",
                           "thumb_s3_key": "images/1/1_thumb.webp"},
                ("1", 2): {"sha256": SHA_C, "photo_version": 1, "serving_s3_key": "x", "thumb_s3_key": None},
                ("9", 1): {"sha256": SHA_C, "photo_version": 1, "serving_s3_key": "y", "thumb_s3_key": None}}
    p = plan.plan_photos(photos, present, {"1", "9"}, existing)
    assert [r["sha256"] for r in p.upserts] == [SHA_A]  # 1번 칸 사진 교체
    assert p.deletes == [("1", 2)]  # emptied. Silver 에 없는 공고 9 는 그대로
    assert p.notes["no_notice"] == 1  # 공고 3 은 RDS 에 없어 외래키로 넣을 수 없음


def test_plan_photos_unchanged():
    photos, present = plan.current_photos([photo("1", 1, SHA_A)])
    same = {("1", 1): {c: photos[("1", 1)][c] for c in plan.PHOTO_COMPARE}}
    assert plan.plan_photos(photos, present, {"1"}, same).unchanged == 1


def test_image_vectors_follow_current_photo():
    photos = {("1", 1): {**photo("1", 1, SHA_A), "photo_version": 1},
              ("1", 2): {**photo("1", 2, SHA_B), "photo_version": 1},
              ("2", 1): {**photo("2", 1, SHA_C), "photo_version": 1}}
    vectors = {SHA_A: {"embedding": [0.1, 0.2], "gold_run_id": "g1"}}
    existing = {("1", 2): SHA_C, ("2", 1): SHA_C, ("8", 1): SHA_A}
    p = plan.plan_image_vectors(photos, vectors, existing, "image_embedding@1")
    assert [(r["desertion_no"], r["image_slot"]) for r in p.upserts] == [("1", 1)]
    up = p.upserts[0]
    assert up["version"] == "image_embedding@1" and up["s3_key"].startswith("bronze/") and "photo_run_id" not in up
    # 1-2 는 사진이 B 로 바뀌었는데 B 벡터가 없다 → 옛 벡터 삭제(검색 후보에서 뺌). 8-1 은 사진 칸이 없다
    assert p.deletes == [("1", 2), ("8", 1)]
    assert p.unchanged == 1 and p.notes["waiting_vector"] == 1


def test_representative_is_smallest_slot():
    photos = {("1", 3): {"sha256": SHA_C}, ("1", 2): {"sha256": SHA_B}, ("2", 1): {"sha256": SHA_A}}
    assert plan.representative(photos) == {"1": SHA_B, "2": SHA_A}


def gold_tag(subject_type, key, task, code, source="photo", source_hash=None, searchable=True, displayable=True):
    return {"subject_type": subject_type, "subject_key": key, "task": task, "tag_code": code, "source": source,
            "source_hash": source_hash or key, "searchable": searchable, "displayable": displayable,
            "rank": None, "similarity": None, "gold_run_id": "run1"}


def test_plan_tags_current_inputs_only():
    rep = {"1": SHA_A, "2": SHA_A, "3": SHA_B}
    gold = [gold_tag("photo", SHA_A, "photo_trait", "ear.drop"),
            gold_tag("photo", SHA_C, "photo_trait", "ear.prick"),  # 대표 사진이 아닌 사진
            gold_tag("photo", SHA_B, "photo_breed", "breed.진도견", searchable=False, displayable=False),
            gold_tag("animal", "1", "text_personality", "personality.gentle", "text", "h-new"),
            gold_tag("animal", "1", "text_personality", "personality.timid", "text", "h-old"),  # 이전 글
            gold_tag("photo", SHA_A, "photo_cute", "cute")]  # 활성 아님
    active = {"photo_trait": "photo_trait@1", "photo_breed": "photo_breed@1", "text_personality": "text_personality@1"}
    labels = {"ear.drop": "늘어진 귀", "personality.gentle": "순함"}
    existing = {("9", "photo", SHA_A, "photo_trait", "ear.drop"): {}}
    p = plan.plan_tags({"1", "2", "3"}, rep, {("1", "text_personality"): "h-new"}, gold, active, labels, existing)
    keys = [(r["desertion_no"], r["tag_code"]) for r in p.upserts]
    assert keys == [("1", "personality.gentle"), ("1", "ear.drop"), ("2", "ear.drop"), ("3", "breed.진도견")]
    by = {(r["desertion_no"], r["tag_code"]): r for r in p.upserts}
    assert by[("3", "breed.진도견")]["tag_label"] == "진도견" and by[("1", "ear.drop")]["version"] == "photo_trait@1"
    assert p.deletes == [("9", "photo", SHA_A, "photo_trait", "ear.drop")]
    assert p.notes["not_current"] == 2


def test_cute_never_searchable():
    gold = [gold_tag("photo", SHA_A, "photo_trait", "impression.cute")]
    p = plan.plan_tags({"1"}, {"1": SHA_A}, {}, gold, {"photo_trait": "photo_trait@1"}, {}, {})
    assert p.upserts[0]["searchable"] is False and p.notes["cute_blocked"] == 1


def test_plan_tags_unchanged_rows_kept():
    gold = [gold_tag("photo", SHA_A, "photo_trait", "ear.drop")]
    first = plan.plan_tags({"1"}, {"1": SHA_A}, {}, gold, {"photo_trait": "photo_trait@1"}, {}, {})
    row = first.upserts[0]
    existing = {tuple(row[c] for c in plan.TAG_KEY): {c: row[c] for c in plan.TAG_COMPARE}}
    again = plan.plan_tags({"1"}, {"1": SHA_A}, {}, gold, {"photo_trait": "photo_trait@1"}, {}, existing)
    assert again.upserts == [] and again.deletes == [] and again.unchanged == 1


def test_text_vectors_latest_per_notice():
    t1, t2 = T0, T0 + timedelta(hours=1)
    gold = [{"subject_key": "1", "source_hash": SHA_A, "embedding": [1.0], "gold_run_id": "r1", "gold_updated_at": t1},
            {"subject_key": "1", "source_hash": SHA_B, "embedding": [2.0], "gold_run_id": "r2", "gold_updated_at": t2},
            {"subject_key": "2", "source_hash": SHA_C, "embedding": [3.0], "gold_run_id": "r3", "gold_updated_at": t1},
            {"subject_key": "7", "source_hash": SHA_C, "embedding": [3.0], "gold_run_id": "r3", "gold_updated_at": t1}]
    p = plan.plan_text_vectors({"1", "2"}, gold, {"2": SHA_C}, "text_embedding@3")
    assert [(r["desertion_no"], r["text_sha256"]) for r in p.upserts] == [("1", SHA_B)]
    assert p.unchanged == 1 and p.notes["no_notice"] == 1
