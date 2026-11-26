"""구조동물 공공 API(data.go.kr 15098931) 호출 도우미. 인증키는 .env 의 DATA_GO_KR_SERVICE_KEY 에서만 읽는다.

scripts/rescue/rescue_api.py 와 같은 호출 방식이다. 수집 코드가 src 안으로 들어오면서 scripts 를 불러 쓸 수 없어 따로 둔다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import requests
from dotenv import load_dotenv

load_dotenv()

RESCUE_URL = "https://apis.data.go.kr/1543061/abandonmentPublicService_v2/abandonmentPublic_v2"

# data.go.kr 게이트웨이 오류 코드 중 수집에서 구분해서 다루는 것
ERROR_HINTS = {
    "10": "파라미터 이름·형식 확인",
    "20": "활용신청 승인 여부, 키 포함 여부 확인",
    "22": "일일 호출 한도 초과",
    "23": "초당 호출 한도 초과, 잠시 후 재시도",
    "30": "키 오타 또는 승인 반영 전",
}


@dataclass
class ApiResult:
    status_code: int
    result_code: str | None = None
    total_count: int | None = None
    items: list[dict[str, Any]] = field(default_factory=list)
    error_name: str | None = None  # 게이트웨이 오류 이름(errMsg). 예: SERVICE_KEY_IS_NOT_REGISTERED_ERROR
    body_kind: str | None = None  # 본문 최상위 키 이름(response / OpenAPI_ServiceResponse 등). 본문 내용은 보관하지 않는다

    @property
    def ok(self) -> bool:
        return self.status_code == 200 and self.result_code in ("00", "0", "000")


def _service_key() -> str:
    key = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not key:
        raise SystemExit(".env 에 DATA_GO_KR_SERVICE_KEY 가 비어 있습니다.")
    # Encoding 키(%2B 등 포함)를 넣었으면 Decoding 형태로 되돌린다. requests 가 다시 인코딩한다.
    return unquote(key) if "%" in key else key


def call(params: dict[str, Any] | None = None, timeout: float = 30.0, url: str = RESCUE_URL) -> ApiResult:
    query: dict[str, Any] = {"serviceKey": _service_key(), "_type": "json", "pageNo": 1, "numOfRows": 10}
    query.update(params or {})
    resp = requests.get(url, params=query, timeout=timeout)  # 오류 메시지에 키가 담기므로 호출하는 쪽은 예외 종류만 출력한다
    result = ApiResult(status_code=resp.status_code)
    try:
        data = resp.json()
    except ValueError:
        return result
    if not isinstance(data, dict) or not data:
        return result
    result.body_kind = next(iter(data))
    if "OpenAPI_ServiceResponse" in data:
        # 게이트웨이 오류(키 오류 등)는 HTTP 401·403 등으로 오고, 정상 응답과 모양이 다르다.
        #   {"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"errMsg": ..., "returnReasonCode": "30"}}}
        header = (data["OpenAPI_ServiceResponse"] or {}).get("cmmMsgHeader") or {}
        code = header.get("returnReasonCode")
        result.result_code = str(code) if code is not None else None
        result.error_name = header.get("errMsg")
        return result
    if "response" not in data:
        return result
    body = data["response"]
    result.result_code = str(body.get("header", {}).get("resultCode"))
    b = body.get("body", {})
    result.total_count = int(b["totalCount"]) if b.get("totalCount") is not None else None
    items = (b.get("items") or {})
    items = items.get("item", []) if isinstance(items, dict) else []
    result.items = [i for i in ([items] if isinstance(items, dict) else items) if isinstance(i, dict)]
    return result


def real_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """페이지 끝의 빈 객체 {} 를 뺀 실제 항목."""
    return [i for i in items if i]


def explain_error(result: ApiResult) -> str:
    """로그용 한 줄. HTTP 상태, 본문 모양(최상위 키 이름), 결과 코드, 오류 이름, 힌트만 담는다. 키나 본문 내용은 넣지 않는다."""
    hint = ERROR_HINTS.get(result.result_code or "")
    parts = [f"status={result.status_code}"]
    if result.body_kind:
        parts.append(f"본문={result.body_kind}")
    parts.append(f"resultCode={result.result_code}")
    detail = ", ".join(x for x in (result.error_name, hint) if x)
    return ", ".join(parts) + (f" ({detail})" if detail else "")
