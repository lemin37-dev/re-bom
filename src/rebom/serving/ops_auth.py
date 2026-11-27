"""운영 모니터링 화면(/ops)의 간단한 비밀번호 로그인.

- 비밀번호는 저장하지 않고 PBKDF2-SHA256 해시만 둔다: REBOM_OPS_PASSWORD_HASH (K8s Secret 에서 환경변수로).
- 로그인하면 서명한 쿠키(rebom_ops)를 준다: "<만료 epoch>.<HMAC-SHA256>". 서명 키는 REBOM_OPS_SESSION_SECRET.
  서버에 세션을 두지 않아 파드가 여러 개여도 되고, 서명 키를 바꾸면 모든 로그인이 풀린다.
- 둘 중 하나라도 비어 있으면 /ops 는 503(설정 안 됨)이다. 비밀번호 없이 열리지 않는다.
- 틀린 비밀번호가 한 주소에서 10분에 5번이면 그 주소는 10분 동안 429.

해시 만들기 (사람이, 비밀번호는 화면에 나오지 않음):
    python -m rebom.serving.ops_auth hash
    → pbkdf2_sha256$600000$<salt>$<hash>   (이 줄을 Secret 의 REBOM_OPS_PASSWORD_HASH 로)
서명 키 만들기:
    python -m rebom.serving.ops_auth secret
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

COOKIE = "rebom_ops"
ALGO = "pbkdf2_sha256"
ITERATIONS = 600_000
MAX_FAILURES = 5
WINDOW_SECONDS = 600


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, *, iterations: int = ITERATIONS, salt: bytes | None = None) -> str:
    if not password:
        raise ValueError("빈 비밀번호")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGO}${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.strip().split("$")
        if algo != ALGO:
            return False
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _unb64(salt), int(iterations))
        return hmac.compare_digest(got, _unb64(digest))
    except (ValueError, TypeError):
        return False


def _sign(secret: str, payload: str) -> str:
    return _b64(hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest())


def make_token(secret: str, hours: float, now: float | None = None) -> str:
    expires = int((time.time() if now is None else now) + hours * 3600)
    return f"{expires}.{_sign(secret, str(expires))}"


def token_valid(secret: str, token: str | None, now: float | None = None) -> bool:
    if not secret or not token or "." not in token:
        return False
    expires, sig = token.split(".", 1)
    if not expires.isdigit() or not hmac.compare_digest(sig, _sign(secret, expires)):
        return False
    return int(expires) > (time.time() if now is None else now)


@dataclass
class LoginLimiter:
    """주소별로 최근 실패 시각을 둔다(파드 메모리. 재시작하면 비워짐)."""

    max_failures: int = MAX_FAILURES
    window: float = WINDOW_SECONDS
    _fails: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))

    def _trim(self, key: str, now: float) -> deque:
        q = self._fails[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, key: str, now: float | None = None) -> bool:
        return len(self._trim(key, time.time() if now is None else now)) >= self.max_failures

    def failed(self, key: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._trim(key, now).append(now)

    def succeeded(self, key: str) -> None:
        self._fails.pop(key, None)


def main(argv: list[str]) -> int:
    if argv == ["secret"]:
        print(secrets.token_urlsafe(48))
        return 0
    if argv != ["hash"]:
        print("사용법: python -m rebom.serving.ops_auth hash|secret", file=sys.stderr)
        return 64
    import getpass

    first = getpass.getpass("운영 화면 비밀번호: ")
    if first != getpass.getpass("한 번 더: "):
        print("두 번 입력한 값이 다릅니다", file=sys.stderr)
        return 1
    if len(first) < 12:
        print("12자 이상으로 정해 주세요", file=sys.stderr)
        return 1
    print(hash_password(first))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
