"""run_text_tags·text_tags·jev_client: 글 정리, 대상 고르기, 판정, 요청 묶기, 멈춤 조건, Decisions 호출. 실제 호출 없음."""
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rebom.common.openrouter import AccountError, OpenRouterError
from rebom.tagging import jev_client
from rebom.tagging import run_text_tags as rtt
from rebom.tagging import text_tags as tt

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "tagging"
P, H = tt.TASK_PERSONALITY, tt.TASK_HEALTH
TS = datetime(2026, 11, 23, tzinfo=UTC)


def fixture_result(**overrides) -> jev_client.DecisionResult:
    data = json.loads((FIX / "jev_decision_response.json").read_text(encoding="utf-8"))
    answers = {**data["answers"], **overrides}
    return jev_client.DecisionResult(answers, data["id"], data["model"], data["provider"], 3919, 170, 0.000165, 400, 1, data)


def notice(no, text, status="PROTECTING", species="417000", special=None):
    return {"desertion_no": no, "status_group": status, "species_code": species, "info_text": text, "special_mark": special}


def log_row(no, task, h, status, attempts=1):
    return {"subject_type": "animal", "subject_key": no, "task": task, "version": rtt.REGISTERED[task], "input_hash": h,
            "status": status, "attempts": attempts}


# ------------------------------------------------------------ 질문지


def test_questions_v1_has_nine_questions_and_versions():
    assert len(tt.QUESTIONS) == 9 and tt.HEALTH_QUESTION in tt.QUESTIONS
    assert tt.VERSIONS == {P: "text-personality-v1", H: "text-health-v1"}
    assert {q["type"] for k, q in tt.QUESTIONS.items() if k != tt.HEALTH_QUESTION} == {"noul"}
    assert tt.QUESTIONS[tt.HEALTH_QUESTION]["type"] == "choice"
    assert len({t.code for t in tt.PERSONALITY_TAGS.values()}) == 8


# ------------------------------------------------------------ 글 정리


def test_clean_text_masks_phone_and_falls_back_to_special_mark():
    assert rtt.clean_text("순함  문의 010-1234-5678\n입질 없음") == "순함 문의 [번호] 입질 없음"
    assert rtt.clean_text(None, "겁 많음") == "겁 많음"
    assert rtt.clean_text("  ", "겁 많음") == "겁 많음"
    assert rtt.clean_text("관리번호 12-345 순함 문의 010-1234-5678") == "순함 문의 [번호]"  # 앞 관리번호 지움


@pytest.mark.parametrize("text", ["관리번호 12-345 순함 문의 010-1234-5678", "개체관리번호 25233 - 겁 많음", "24-7521 온순함.",
                                  "  사람 좋아함 \n 010.1234.5678 ", "순함, 관리번호 12-345 는 앞이 아니라 그대로"])
def test_clean_text_same_as_a2_card_persona(text):
    """A2 글 카드의 특징 글(text_card.persona)과 글자까지 같다 (2026-11-23 결정)."""
    from rebom.embedding.text_card import persona

    assert rtt.clean_text(text) == persona({"specialMark": text})


@pytest.mark.parametrize("text,blank", [(".", True), ("없음", True), ("해당없음", True), ("특이사항 없음", True), ("-", True),
                                        ("", True), ("이상 없음", False), ("순함", False), ("없음 순함", False)])
def test_is_blank(text, blank):
    assert rtt.is_blank(text) is blank


# ------------------------------------------------------------ 대상 고르기


def test_select_filters_status_species_and_blank_text():
    rows = [notice("1", "순함"), notice("2", "순함", status="CLOSED"), notice("3", "순함", species="429900"),
            notice("4", "없음"), notice("5", "겁많음", species="422400")]
    targets, no_text = rtt.select_targets(rows, [])
    assert [t.desertion_no for t in targets] == ["1", "5"]
    assert no_text == ["4"]
    assert targets[0].tasks == (P, H)


def test_select_skips_done_and_retags_changed_text():
    h = rtt.text_hash("순함")
    logs = [log_row("1", P, h, "OK"), log_row("1", H, h, "OK"), log_row("2", P, rtt.text_hash("예전 글"), "OK"),
            log_row("2", H, rtt.text_hash("예전 글"), "OK")]
    targets, _ = rtt.select_targets([notice("1", "순함"), notice("2", "새 글")], logs)
    assert [t.desertion_no for t in targets] == ["2"]  # 1 은 같은 글로 끝남, 2 는 글이 바뀜


def test_select_retries_failed_up_to_three_and_keeps_missing_task():
    h = rtt.text_hash("순함")
    logs = [log_row("1", P, h, "FAILED", 2), log_row("1", H, h, "OK"), log_row("2", P, h, "FAILED", 3),
            log_row("2", H, h, "FAILED", 3)]
    targets, _ = rtt.select_targets([notice("1", "순함"), notice("2", "순함")], logs)
    assert [(t.desertion_no, t.tasks) for t in targets] == [("1", (P,))]


def test_no_text_written_once():
    logs = [log_row("4", P, rtt.NO_TEXT_HASH, "NO_TEXT"), log_row("4", H, rtt.NO_TEXT_HASH, "NO_TEXT")]
    _, no_text = rtt.select_targets([notice("4", ".")], logs)
    assert no_text == []


# ------------------------------------------------------------ 판정


def test_decide_thresholds_review_and_health():
    d = tt.decide(fixture_result().answers)
    assert [(t.label, p) for t, p in d.personality] == [("순함", 0.97), ("활발", 0.81)]
    assert d.review == ["친화적"]  # 0.62
    assert d.health.code == "health.info" and d.health_prob == 0.92 and not d.health_review


def test_decide_opposite_tags_both_kept_and_health_none():
    d = tt.decide(fixture_result(trait_aggr={"type": "noul", "noul": 0.9},
                                 health_info={"type": "choice", "probabilities": {"정보 없음": 0.7, "정보 있음": 0.3}}).answers)
    assert [t.label for t, _ in d.personality] == ["순함", "사나움", "활발"]  # 반대 뜻이어도 둘 다 붙인다
    assert "conflict" not in json.loads(tt.personality_detail(d, "q"))
    assert d.health.code == "health.none" and d.health.searchable and d.health.displayable  # 두 값 모두 라벨로 씀


def test_decide_bad_answer_raises():
    with pytest.raises(KeyError):
        tt.decide({"trait_soon": {"noul": 0.9}})


# ------------------------------------------------------------ 호출·행 만들기


class FakeClient:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def decide(self, text, questions):
        self.calls.append(text)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def targets_for(*pairs):
    return [rtt.Target(no, rtt.text_hash(text), text, (P, H)) for no, text in pairs]


def test_same_text_called_once_and_rows_for_each_animal():
    groups = rtt.group_by_text(targets_for(("1", "순함"), ("2", "순함"), ("3", "겁 많음")))
    client = FakeClient([fixture_result(), fixture_result()])
    outcomes, stop = rtt.tag_texts(groups, client, "run1", concurrency=1, now=lambda: TS)
    assert stop is None and len(client.calls) == 2
    scope, rows = rtt.replaced_tag_rows(outcomes)
    assert sorted({r["subject_key"] for r in rows}) == ["1", "2", "3"]
    first = [r for r in rows if r["subject_key"] == "1"]
    assert sorted(r["tag_code"] for r in first) == ["health.info", "personality.active", "personality.gentle"]
    g = next(r for r in first if r["tag_code"] == "personality.gentle")
    assert g["source"] == "text" and g["subject_type"] == "animal" and g["similarity"] == 97
    assert g["prompt_version"] == "text-personality-v1" and g["source_hash"] == rtt.text_hash("순함")
    assert g["version"] == "text_personality@1" and scope[0][2] in ("text_personality@1", "text_health@1")
    detail = json.loads(g["detail"])
    assert detail["request_id"] == "gen-test-0001" and detail["input_text"] == "순함"
    h = next(r for r in first if r["tag_code"] == "health.info")
    assert json.loads(h["detail"])["input_text"] == "순함"
    assert ("2", P, "text_personality@1", rtt.text_hash("순함")) in scope
    reqs = [o.request for o in outcomes]
    assert len(reqs) == 2 and reqs[0]["task"] == "text_jev" and reqs[0]["prompt_version"] == "text-personality-v1+text-health-v1"
    assert json.loads(reqs[0]["raw_answer"])["trait_soon"]["noul"] == 0.97
    logs = rtt.inference_rows(outcomes, {}, "run1", TS)
    assert len(logs) == 6 and {r["status"] for r in logs} == {"OK"}
    assert next(r for r in logs if r["subject_key"] == "1" and r["task"] == P)["tag_count"] == 2


def test_no_personality_tag_is_no_result():
    low = {q: {"type": "noul", "noul": 0.1} for q in tt.PERSONALITY_TAGS}
    outcomes, _ = rtt.tag_texts(rtt.group_by_text(targets_for(("1", "목줄 착용"))), FakeClient([fixture_result(**low)]), "r",
                                concurrency=1, now=lambda: TS)
    assert outcomes[0].statuses == {P: "NO_RESULT", H: "OK"}
    scope, rows = rtt.replaced_tag_rows(outcomes)
    assert [r["tag_code"] for r in rows] == ["health.info"]
    assert ("1", P, "text_personality@1", rtt.text_hash("목줄 착용")) in scope


def test_failed_text_not_replaced_and_counted():
    outcomes, stop = rtt.tag_texts(rtt.group_by_text(targets_for(("1", "순함"))), FakeClient([OpenRouterError("HTTP 400")]),
                                   "r", concurrency=1, now=lambda: TS)
    assert stop is None and outcomes[0].statuses == {P: "FAILED", H: "FAILED"}
    assert rtt.replaced_tag_rows(outcomes) == ([], [])
    logs = rtt.inference_rows(outcomes, {("animal", "1", P, "text_personality@1", rtt.text_hash("순함")): {"attempts": 2}}, "r", TS)
    assert next(r for r in logs if r["task"] == P)["attempts"] == 3


def test_account_error_stops_and_drops_rest():
    groups = rtt.group_by_text(targets_for(("1", "a"), ("2", "b"), ("3", "c")))
    client = FakeClient([fixture_result(), AccountError("402", status=402), fixture_result()])
    outcomes, stop = rtt.tag_texts(groups, client, "r", concurrency=1, now=lambda: TS)
    assert isinstance(stop, AccountError)
    assert [o.targets[0].desertion_no for o in outcomes] == ["1"]  # 멈춘 뒤의 글은 다음 실행에서
    assert len(client.calls) == 2


def test_no_text_rows_and_local_sink_roundtrip(tmp_path):
    sink = rtt.LocalSink(tmp_path)
    sink.write([], [], rtt.no_text_rows(["4"], "r", TS), [])
    _, no_text = rtt.select_targets([notice("4", "없음")], sink.read_inference_log())
    assert no_text == []


# ------------------------------------------------------------ Decisions 호출


class FakeResp:
    def __init__(self, status, data, headers=None):
        self.status_code, self._data, self.headers = status, data, headers or {}

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, responses):
        self.responses, self.bodies = list(responses), []

    def post(self, url, json=None, headers=None, timeout=None):
        self.bodies.append((url, json, headers))
        return self.responses.pop(0)


def test_jev_client_body_and_parse():
    data = json.loads((FIX / "jev_decision_response.json").read_text(encoding="utf-8"))
    s = FakeSession([FakeResp(200, data)])
    res = jev_client.JevClient("X", key="k", session=s).decide("순함", tt.QUESTIONS)
    url, body, _headers = s.bodies[0]
    assert url.endswith("/api/alpha/decisions") and body["model"] == "typesafe/jev-1.14"
    assert body["provider"] == {"order": ["typesafe"], "allow_fallbacks": False}
    assert body["state"] == {"설명 글": "순함"} and len(body["questions"]) == 9
    assert res.cost_usd == 0.000165 and res.input_tokens == 3919 and res.provider == "TypeSafe"


def test_jev_client_account_error_and_retry():
    with pytest.raises(AccountError):
        jev_client.JevClient("X", key="k", session=FakeSession([FakeResp(402, {"error": {"code": 402}})])).decide("a", {})
    data = json.loads((FIX / "jev_decision_response.json").read_text(encoding="utf-8"))
    waits = []
    s = FakeSession([FakeResp(429, {"error": {"code": 429}}, {"Retry-After": "3"}), FakeResp(200, data)])
    res = jev_client.JevClient("X", key="k", session=s, sleep=waits.append).decide("a", {})
    assert res.attempts == 2 and waits == [3.0]
    with pytest.raises(OpenRouterError):
        jev_client.JevClient("X", key="k", session=FakeSession([FakeResp(400, {"error": {"code": 400}})])).decide("a", {})


def test_registered_versions_and_runtime_config():
    """처리 기록·태그 version 은 레지스트리 이름(task@1). 설정은 민혁 님 fill/legacy 스크립트가 등록한 값과 같다."""
    import importlib.util
    from pathlib import Path

    assert rtt.REGISTERED == {P: "text_personality@1", H: "text_health@1"}
    path = Path(__file__).resolve().parents[2] / "scripts" / "serve" / "legacy_version_config.py"
    spec = importlib.util.spec_from_file_location("legacy_version_config", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for task in (P, H):
        assert rtt.runtime_config(task) == mod.config_for(task)
    rows = rtt.no_text_rows(["1"], "r", rtt._now())
    assert {r["version"] for r in rows} == {"text_personality@1", "text_health@1"}


def test_version_option_parses_and_defaults_to_registered():
    assert rtt.registered_versions([]) == rtt.REGISTERED
    assert rtt.registered_versions(["text_personality@2"]) == {P: "text_personality@2", H: "text_health@1"}
    for bad in (["photo_trait@1"], ["text_personality"], ["text_personality@x"], ["text_health@2", "text_health@3"]):
        with pytest.raises(ValueError):
            rtt.registered_versions(bad)


def test_run_follows_target_file_versions():
    saved = {P: "text_personality@2", H: "text_health@1"}
    assert rtt.run_versions([], saved) == saved  # select 때 정한 버전을 따른다
    assert rtt.run_versions(["text_personality@2"], saved) == saved
    assert rtt.run_versions([], None) == rtt.REGISTERED  # versions 없는 옛 목록
    with pytest.raises(ValueError, match="다릅니다"):
        rtt.run_versions(["text_personality@3"], saved)


def test_candidate_version_goes_to_logs_tags_and_scope():
    v = rtt.registered_versions(["text_personality@2"])
    outcomes, _ = rtt.tag_texts(rtt.group_by_text(targets_for(("1", "순함"))), FakeClient([fixture_result()]), "r",
                                concurrency=1, now=lambda: TS, versions=v)
    scope, rows = rtt.replaced_tag_rows(outcomes, v)
    assert {(r["task"], r["version"]) for r in rows} == {(P, "text_personality@2"), (H, "text_health@1")}
    assert ("1", P, "text_personality@2", rtt.text_hash("순함")) in scope
    logs = rtt.inference_rows(outcomes, {}, "r", TS, v)
    assert {(r["task"], r["version"]) for r in logs} == {(P, "text_personality@2"), (H, "text_health@1")}
    assert {r["version"] for r in rtt.no_text_rows(["9"], "r", TS, v)} == {"text_personality@2", "text_health@1"}
    # @1 로 끝난 처리 기록은 @2 후보에서는 끝난 것으로 보지 않는다
    done = [log_row("1", P, rtt.text_hash("순함"), "OK"), log_row("1", H, rtt.text_hash("순함"), "OK")]
    targets, _ = rtt.select_targets([notice("1", "순함")], done, versions=v)
    assert [t.tasks for t in targets] == [(P,)]
