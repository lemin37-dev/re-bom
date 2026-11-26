"""API 응답 해석과 재시도 시험. 실제 API 는 부르지 않고 가짜 응답을 쓴다. (정상 응답 쪽은 test_rescue_incremental.py)

게이트웨이 오류(키 오류 등)는 HTTP 401·403 으로 오고 본문 모양이 정상 응답과 다르다.
  정상:   {"response": {"header": {"resultCode": "00", ...}, "body": {...}}}
  오류:   {"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"errMsg": ..., "returnAuthMsg": ..., "returnReasonCode": "30"}}}
GATEWAY_30, GATEWAY_20 은 2026-11-09 에 틀린 키·키 없음으로 직접 호출해 받은 응답을 그대로 옮긴 것이다.
코드 22·23 은 실제 응답을 본 적이 없어서, 같은 모양을 가정한 것(ASSUMED_*)으로만 시험한다.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from rebom.ingestion import rescue_incremental as ri
from rebom.ingestion.rescue_api import ApiResult, call, explain_error

SECRET = "SECRET-KEY-VALUE"


def _gateway(code, name, msg="메시지"):
    return {"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"errMsg": name, "returnAuthMsg": msg, "returnReasonCode": code}}}


GATEWAY_30 = _gateway("30", "SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "등록되지 않은 인증키")  # HTTP 403 (실제 호출)
GATEWAY_20 = _gateway("20", "SERVICE_KEY_IS_NULL", "서비스 접근거부")  # HTTP 401 (실제 호출)
ASSUMED_22 = _gateway("22", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR")  # 가정: 실제 응답은 못 봄
ASSUMED_23 = _gateway("23", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR")  # 가정


def _normal(code, total=0, item=None):
    items = {"item": item} if item is not None else ""
    return {"response": {"header": {"resultCode": code}, "body": {"totalCount": total, "items": items}}}


def _http(status, body):
    resp = MagicMock(status_code=status)
    if isinstance(body, Exception):
        resp.json.side_effect = body
    else:
        resp.json.return_value = body
    return resp


def _patch_get(*responses):
    """requests.get 이 responses 를 차례로 돌려준다."""
    return patch("rebom.ingestion.rescue_api.requests.get", side_effect=list(responses))


@pytest.fixture(autouse=True)
def _key_and_no_sleep(monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", SECRET)
    monkeypatch.setattr(ri.time, "sleep", lambda s: None)  # 재시도 대기를 건너뛴다


# --- rescue_api.call: 응답 해석 ---

def test_gateway_key_error_403_is_read_from_return_reason_code():
    with _patch_get(_http(403, GATEWAY_30)):
        r = call({})
    assert r.status_code == 403
    assert r.result_code == "30"
    assert r.error_name == "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"
    assert r.body_kind == "OpenAPI_ServiceResponse"
    assert r.ok is False


def test_gateway_key_missing_401_is_read_from_return_reason_code():
    with _patch_get(_http(401, GATEWAY_20)):
        r = call({})
    assert (r.status_code, r.result_code, r.error_name) == (401, "20", "SERVICE_KEY_IS_NULL")
    assert r.ok is False


def test_normal_response_is_unchanged():
    with _patch_get(_http(200, _normal("00", 2, [{"desertionNo": "1"}, {}]))):
        r = call({})
    assert r.ok and r.total_count == 2
    assert r.items == [{"desertionNo": "1"}, {}]  # 빈 객체는 real_items 가 뺀다
    assert r.body_kind == "response"
    assert r.error_name is None


def test_http_200_with_error_code_in_normal_shape_is_not_ok():
    with _patch_get(_http(200, _normal("30"))):
        r = call({})
    assert r.result_code == "30" and r.ok is False


def test_non_json_body_has_no_result_code_and_is_not_ok():
    with _patch_get(_http(200, ValueError("not json"))):
        r = call({})
    assert r.result_code is None and r.body_kind is None and r.ok is False


def test_non_200_is_not_ok_even_if_code_is_00():
    with _patch_get(_http(503, _normal("00"))):
        assert call({}).ok is False


def test_unknown_json_shape_keeps_top_level_key_name_only():
    with _patch_get(_http(500, {"message": "x", "secret": SECRET})):
        r = call({})
    assert r.result_code is None and r.body_kind == "message"  # 본문 내용이 아니라 최상위 키 이름만


# --- explain_error: 로그 문구 ---

def test_explain_error_has_status_shape_code_name_hint_and_no_key():
    with _patch_get(_http(403, GATEWAY_30)):
        text = explain_error(call({}))
    for part in ("403", "OpenAPI_ServiceResponse", "30", "SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "키 오타"):
        assert part in text
    assert SECRET not in text


def test_explain_error_for_unreadable_body_still_shows_status():
    text = explain_error(ApiResult(status_code=502))
    assert "502" in text and "None" in text


# --- Fetcher._get: 재시도와 중단 ---

def test_retries_after_key_error_then_succeeds():
    with _patch_get(_http(403, GATEWAY_30), _http(200, _normal("00", 0))) as m:
        r = ri.Fetcher(max_calls=10)._get({})
    assert r.ok and m.call_count == 2


def test_gives_up_after_4_failures():
    with _patch_get(*[_http(403, GATEWAY_30)] * 4) as m:
        with pytest.raises(ri.ApiFailure):
            ri.Fetcher(max_calls=10)._get({"state": "protect", "bgnde": "20260101"})
    assert m.call_count == 4


@pytest.mark.parametrize(
    "status,body",
    [(429, ASSUMED_22), (403, ASSUMED_22), (200, _normal("22"))],
    ids=["429-gateway", "403-gateway", "200-normal-shape"],
)
def test_daily_limit_code_22_stops_without_retry_in_any_assumed_shape(status, body):
    """22 가 어떤 HTTP 상태와 모양으로 와도(실제는 미확인) 재시도 없이 중단한다."""
    with _patch_get(_http(status, body)) as m:
        with pytest.raises(SystemExit) as exc:
            ri.Fetcher(max_calls=10)._get({})
    assert m.call_count == 1
    # 22 의 실제 응답 모양을 처음 만났을 때 중단 메시지만 보고 알 수 있어야 한다
    assert str(status) in str(exc.value) and "resultCode=22" in str(exc.value)
    assert SECRET not in str(exc.value)


def test_per_second_limit_code_23_is_retried():
    with _patch_get(_http(429, ASSUMED_23), _http(200, _normal("00", 0))) as m:
        r = ri.Fetcher(max_calls=10)._get({})
    assert r.ok and m.call_count == 2


def test_request_exception_is_retried_and_message_has_no_key(capsys):
    boom = requests.ConnectionError(f"https://x?serviceKey={SECRET}")
    with patch("rebom.ingestion.rescue_api.requests.get", side_effect=[boom, _http(200, _normal("00", 0))]) as m:
        r = ri.Fetcher(max_calls=10)._get({})
    assert r.ok and m.call_count == 2
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err  # 예외 메시지(URL·키)는 출력하지 않고 종류만 남긴다


def test_failure_log_line_has_status_shape_code_name_and_no_key(capsys):
    with _patch_get(_http(403, GATEWAY_30), _http(200, _normal("00", 0))):
        ri.Fetcher(max_calls=10)._get({})
    out = capsys.readouterr().out
    for part in ("403", "OpenAPI_ServiceResponse", "resultCode=30", "SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "재시도 1/4"):
        assert part in out
    assert SECRET not in out


def test_stops_when_max_calls_exceeded():
    with _patch_get(*[_http(403, GATEWAY_30)] * 3):
        with pytest.raises(SystemExit):
            ri.Fetcher(max_calls=2)._get({})
