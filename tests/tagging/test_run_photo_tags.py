"""run_photo_tags: 대표 사진 고르기, 다시 부르는 조건, 호출 결과 → 태그·처리 기록·요청 기록, 멈춤 조건. 실제 호출 없음."""
import io
import json
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from rebom.common.openrouter import AccountError, ChatResult, OpenRouterError
from rebom.tagging import image_input
from rebom.tagging import run_photo_tags as rpt

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "tagging"
TRAIT, BREED = rpt.trait_tags.TASK, rpt.breed_tags.TASK
TV, BV = rpt.VERSIONS[TRAIT], rpt.VERSIONS[BREED]


def img_row(no, slot, sha="a" * 64, key=None):
    return {"desertion_no": no, "image_slot": slot, "original_s3_key": key or f"bronze/rescue/images/{no}/popfile{slot}.jpg",
            "sha256": sha}


def log_row(sha, task, version, status, attempts=1):
    """사진 처리 기록: 대상은 사진 sha256 (subject_type=photo)."""
    return {"subject_type": "photo", "subject_key": sha, "task": task, "version": version, "input_hash": sha,
            "status": status, "attempts": attempts}


# ------------------------------------------------------------ 대상 고르기


def test_representative_is_smallest_stored_slot():
    rows = [img_row("1", 2, "b" * 64), img_row("1", 7), {**img_row("1", 1), "original_s3_key": None}]
    rep = rpt.representative_photos(rows)
    assert rep["1"]["image_slot"] == 2


def test_uses_versioned_key_from_silver_as_is():
    rows = [img_row("1", 1, key="bronze/rescue/images/1/popfile1_v002.jpg")]
    targets, _ = rpt.select_targets(["1"], rows, [])
    assert targets[0].s3_key.endswith("popfile1_v002.jpg")


def test_same_photo_on_two_notices_is_called_once():
    """같은 사진(sha256)을 대표 사진으로 쓰는 공고가 둘이면 한 번만 부른다."""
    rows = [img_row("2", 1, "a" * 64), img_row("1", 1, "a" * 64), img_row("3", 1, "c" * 64)]
    targets, _ = rpt.select_targets(["1", "2", "3"], rows, [])
    assert [(t.sha256[:1], t.desertion_nos) for t in targets] == [("a", ("1", "2")), ("c", ("3",))]


def test_skip_done_and_retag_when_hash_or_version_changes():
    sha_old, sha_new = "a" * 64, "c" * 64
    logs = [log_row(sha_old, TRAIT, TV, "OK"), log_row(sha_old, BREED, BV, "NO_RESULT")]
    targets, _ = rpt.select_targets(["1"], [img_row("1", 1, sha_old)], logs)
    assert targets == []
    targets, _ = rpt.select_targets(["1"], [img_row("1", 1, sha_new)], logs)
    assert targets[0].tasks == (TRAIT, BREED) and targets[0].sha256 == sha_new
    targets, _ = rpt.select_targets(["1"], [img_row("1", 1, sha_old)], logs, versions={TRAIT: "trait-vision-v11", BREED: BV})
    assert targets[0].tasks == (TRAIT,)


def test_photo_done_for_one_notice_is_done_for_another():
    """다른 공고에서 이미 태깅한 사진이면 새 공고에 올라와도 다시 부르지 않는다."""
    sha = "a" * 64
    logs = [log_row(sha, TRAIT, TV, "OK"), log_row(sha, BREED, BV, "OK")]
    assert rpt.select_targets(["9"], [img_row("9", 1, sha)], logs)[0] == []


def test_failed_is_retried_until_three_attempts():
    sha = "a" * 64
    two = [log_row(sha, TRAIT, TV, "FAILED", 2), log_row(sha, BREED, BV, "OK")]
    assert rpt.select_targets(["1"], [img_row("1", 1, sha)], two)[0][0].tasks == (TRAIT,)
    three = [log_row(sha, TRAIT, TV, "FAILED", 3), log_row(sha, BREED, BV, "OK")]
    assert rpt.select_targets(["1"], [img_row("1", 1, sha)], three)[0] == []


def test_no_photo_recorded_once_per_notice():
    targets, no_photo = rpt.select_targets(["2", "1"], [img_row("1", 1)], [])
    assert [t.desertion_nos for t in targets] == [("1",)] and no_photo == ["2"]
    logs = rpt.no_photo_rows(no_photo, "r", datetime.now(UTC))
    assert {(r["subject_type"], r["subject_key"]) for r in logs} == {("animal", "2")}
    assert rpt.select_targets(["2"], [], logs)[1] == []


def test_limit_keeps_sorted_order():
    rows = [img_row(n, 1, n * 64) for n in ("3", "1", "2")]
    targets, _ = rpt.select_targets(["3", "1", "2"], rows, [], limit=2)
    assert [t.desertion_nos for t in targets] == [("1",), ("2",)]


def test_pending_targets_drops_tasks_done_since_select():
    t = rpt.Target("a" * 64, "k", (TRAIT, BREED), ("1",))
    assert rpt.pending_targets([t], [log_row("a" * 64, TRAIT, TV, "OK")])[0].tasks == (BREED,)
    assert rpt.Target.from_dict({**rpt.asdict(t)}) == t


# ------------------------------------------------------------ 호출 → 결과


def jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="JPEG")
    return buf.getvalue()


def result_from(name, **over):
    raw = json.loads((FIX / name).read_text(encoding="utf-8"))
    u = raw["usage"]
    kw = {
        "content": raw["choices"][0]["message"]["content"], "request_id": raw["id"], "requested_model": rpt.MODEL,
        "response_model": raw["model"], "provider": raw["provider"], "prompt_tokens": u["prompt_tokens"],
        "completion_tokens": u["completion_tokens"],
        "reasoning_tokens": u["completion_tokens_details"]["reasoning_tokens"],
        "cost_usd": u["cost"], "latency_ms": 1700, "attempts": 1, "raw": raw,
        "finish_reason": raw["choices"][0].get("finish_reason"),
        "native_finish_reason": raw["choices"][0].get("native_finish_reason"),
    }
    kw.update(over)
    return ChatResult(**kw)


class FakeClient:
    def __init__(self, trait=None, breed=None):
        self.trait = trait or (lambda: result_from("openrouter_trait_response.json"))
        self.breed = breed or (lambda: result_from("openrouter_breed_response.json"))
        self.requests = []

    def chat(self, req):
        self.requests.append(req)
        out = self.trait() if req.schema_name == "trait" else self.breed()
        if isinstance(out, Exception):
            raise out
        return out


def target(no="1", tasks=(TRAIT, BREED)):
    return rpt.Target(no * 64, f"k/{no}.jpg", tasks, (no,))


def cfg():
    return rpt.TaggerConfig(run_id="run-1", concurrency=2, canonical_slug="google/gemini-3.1-flash-lite-20260507")


def test_tag_photos_makes_four_requests_and_tags():
    client = FakeClient()
    outcomes, stop = rpt.tag_photos([(target(), jpeg())], client, cfg())
    assert stop is None and len(client.requests) == 4
    assert all(r.media_resolution == "MEDIA_RESOLUTION_MEDIUM" and r.provider == "google-ai-studio" for r in client.requests)
    assert {r.temperature for r in client.requests} == {1.0}
    o = outcomes[0]
    assert o.tasks[TRAIT].status == "OK"
    assert [r["tag_code"] for r in o.tasks[TRAIT].tag_rows] == ["fur.short", "ear.prick", "eye.sharp"]
    breed_rows = o.tasks[BREED].tag_rows
    assert [(r["tag_code"], r["searchable"], r["displayable"], r["similarity"]) for r in breed_rows] == [
        ("breed.진도견", True, False, 92), ("breed.시바", False, False, 70)]
    assert len(o.requests) == 4 and {r["cost_usd"] for r in o.requests} == {0.000697, 0.000953}
    assert all(r["canonical_slug"] and r["media_resolution"] == "MEDIA_RESOLUTION_MEDIUM" for r in o.requests)
    assert {(r["temperature"], r["reasoning_effort"], r["finish_reason"]) for r in o.requests} == {(1.0, "low", "stop")}


def test_bad_answer_marks_task_failed_and_keeps_old_tags():
    client = FakeClient(breed=lambda: result_from("openrouter_breed_response.json", content="{not json"))
    outcomes, stop = rpt.tag_photos([(target(), jpeg())], client, cfg())
    assert stop is None
    o = outcomes[0]
    assert o.tasks[BREED].status == "FAILED" and o.tasks[TRAIT].status == "OK"
    scope, _ = rpt.replaced_tag_rows(outcomes)
    assert scope == [("1" * 64, TRAIT, TV)]  # 품종은 교체하지 않는다
    assert {(r["subject_type"], r["subject_key"]) for r in o.tasks[TRAIT].tag_rows} == {("photo", "1" * 64)}
    assert {(r["subject_type"], r["subject_key"]) for r in o.requests} == {("photo", "1" * 64)}
    assert {r["status"] for r in o.requests if r["task"] == BREED} == {"BAD_ANSWER"}


def test_resolution_guard_stops_run():
    client = FakeClient(trait=lambda: result_from("openrouter_trait_response.json", prompt_tokens=1818))
    outcomes, stop = rpt.tag_photos([(target(), jpeg())], client, cfg())
    assert isinstance(stop, rpt.ResolutionGuardError)
    assert all(not o.tasks for o in outcomes)  # 처리 기록·태그는 쓰지 않는다
    assert rpt.replaced_tag_rows(outcomes) == ([], [])


def test_account_error_stops_run_without_counting_attempts():
    client = FakeClient(trait=lambda: AccountError("Insufficient credits", status=402))
    outcomes, stop = rpt.tag_photos([(target("1"), jpeg()), (target("2"), jpeg())], client, cfg())
    assert isinstance(stop, AccountError)
    assert rpt.inference_rows(outcomes, {}, "run-1", datetime.now(UTC)) == []


def log_rows_of(outcomes):
    return {(r["subject_key"][:1], r["task"]): r for r in rpt.inference_rows(outcomes, {}, "run-1", datetime.now(UTC))}


def test_unopenable_or_oversized_image_is_bad_image_and_not_retried(monkeypatch):
    small = jpeg()
    big = small + bytes(200)
    monkeypatch.setattr(image_input, "MAX_IMAGE_BYTES", len(small) + 100)
    client = FakeClient()
    items = [(target("1"), small), (target("2"), big), (target("3"), b"not an image")]
    outcomes, stop = rpt.tag_photos(items, client, cfg())
    assert stop is None and len(client.requests) == 4  # 1번 사진만 불렀다
    rows = log_rows_of(outcomes)
    assert rows[("2", TRAIT)]["status"] == "BAD_IMAGE"
    assert rows[("2", TRAIT)]["last_error"] == "bad_image:ImageTooLargeError"
    assert rows[("3", BREED)]["last_error"].startswith("bad_image:")
    assert json.loads(rows[("3", TRAIT)]["detail"]) == {"s3_key": "k/3.jpg"}
    assert rpt.is_done(rows[("2", TRAIT)])


def test_model_error_is_failed_with_code_and_input_problems_keep_their_status():
    client = FakeClient(trait=lambda: OpenRouterError("HTTP 400: bad schema", status=400))
    sha_problem = rpt.InputProblem("SHA_MISMATCH", "sha_mismatch", {"s3_key": "k/2.jpg"})
    outcomes, stop = rpt.tag_photos([(target("1", (TRAIT,)), jpeg()), (target("2"), sha_problem)], client, cfg())
    assert stop is None
    rows = log_rows_of(outcomes)
    assert rows[("1", TRAIT)]["status"] == "FAILED" and rows[("1", TRAIT)]["last_error"] == "model_error:400"
    assert "bad schema" in json.loads(rows[("1", TRAIT)]["detail"])["error"]
    assert {rows[("2", TRAIT)]["status"], rows[("2", BREED)]["status"]} == {"SHA_MISMATCH"}
    assert rows[("2", TRAIT)]["last_error"] == "sha_mismatch"


def test_bad_answer_is_failed_with_exception_name():
    client = FakeClient(breed=lambda: result_from("openrouter_breed_response.json", content="{not json"))
    rows = log_rows_of(rpt.tag_photos([(target(), jpeg())], client, cfg())[0])
    assert rows[("1", BREED)]["status"] == "FAILED"
    assert rows[("1", BREED)]["last_error"] == "bad_answer:JSONDecodeError"
    assert rows[("1", TRAIT)]["detail"] is None  # OK 는 detail 을 비운다


def test_read_and_sha_problems_are_retried_every_run_but_bad_image_is_not():
    sha = "a" * 64
    for status, attempts, retried in (("READ_ERROR", 7, True), ("SHA_MISMATCH", 4, True), ("BAD_IMAGE", 1, False),
                                      ("FAILED", 2, True), ("FAILED", 3, False)):
        logs = [log_row(sha, TRAIT, TV, status, attempts), log_row(sha, BREED, BV, "OK")]
        targets, _ = rpt.select_targets(["1"], [img_row("1", 1, sha)], logs)
        assert bool(targets) == retried, (status, attempts)


def test_inference_rows_count_attempts():
    outcomes, _ = rpt.tag_photos([(target(), jpeg())], FakeClient(), cfg())
    prev = rpt.log_index([log_row("1" * 64, TRAIT, TV, "FAILED", 2)])
    rows = {r["task"]: r for r in rpt.inference_rows(outcomes, prev, "run-1", datetime.now(UTC))}
    assert rows[TRAIT]["attempts"] == 3 and rows[TRAIT]["status"] == "OK" and rows[TRAIT]["tag_count"] == 3
    assert rows[BREED]["attempts"] == 1 and rows[BREED]["tag_count"] == 1


def test_fetch_image_checks_sha(tmp_path):
    import hashlib

    data = jpeg()

    class S3:
        def get_object(self, Bucket, Key):
            return {"Body": io.BytesIO(data)}

    ok = rpt.Target(hashlib.sha256(data).hexdigest(), "k", (TRAIT,), ("1",))
    assert rpt.fetch_image(S3(), "b", ok) == data
    bad = rpt.fetch_image(S3(), "b", rpt.Target("0" * 64, "k", (TRAIT,), ("1",)))
    assert (bad.status, bad.last_error, bad.detail) == ("SHA_MISMATCH", "sha_mismatch", {"s3_key": "k"})


def test_fetch_image_read_error_uses_s3_error_code():
    class NoSuchKey(Exception):
        response = {"Error": {"Code": "NoSuchKey"}}

    class S3:
        def get_object(self, Bucket, Key):
            raise NoSuchKey()

    got = rpt.fetch_image(S3(), "b", rpt.Target("0" * 64, "k", (TRAIT,), ("1",)))
    assert (got.status, got.last_error, got.detail) == ("READ_ERROR", "read_error:NoSuchKey", {"s3_key": "k", "error": "NoSuchKey"})


def test_local_sink_round_trip(tmp_path):
    sink = rpt.LocalSink(tmp_path)
    ts = datetime.now(UTC)
    sink.write([], [], rpt.no_photo_rows(["9"], "r", ts), [])
    logs = sink.read_inference_log()
    assert {(r["subject_type"], r["subject_key"], r["status"]) for r in logs} == {("animal", "9", "NO_PHOTO")}
