"""Password login, signed session cookie and a login-attempt throttle.

The cookie is ``<expiry>.<hmac>``; the HMAC key derives from the bot token AND
the password, so changing ``CCGRAM_WEBUI_PASSWORD`` logs every browser out.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import deque

COOKIE_NAME = "ccgram_webui"
SESSION_TTL_S = 30 * 24 * 3600

# Failed logins per client before a lockout, and how long the window is.
MAX_FAILURES = 5
FAILURE_WINDOW_S = 15 * 60


def _key(bot_token: str, password: str) -> bytes:
    return hashlib.sha256(f"webui:{bot_token}:{password}".encode()).digest()


def check_password(candidate: str, password: str) -> bool:
    if not password:
        return False
    return hmac.compare_digest(candidate.encode(), password.encode())


def issue_cookie(bot_token: str, password: str, now: float | None = None) -> str:
    exp = int((now or time.time()) + SESSION_TTL_S)
    sig = hmac.new(_key(bot_token, password), str(exp).encode(), "sha256").hexdigest()
    return f"{exp}.{sig}"


def verify_cookie(
    value: str | None, bot_token: str, password: str, now: float | None = None
) -> bool:
    if not value or not password:
        return False
    exp_s, _, sig = value.partition(".")
    if not exp_s.isdigit() or not sig:
        return False
    if int(exp_s) < (now or time.time()):
        return False
    expected = hmac.new(_key(bot_token, password), exp_s.encode(), "sha256").hexdigest()
    return hmac.compare_digest(sig, expected)


class LoginThrottle:
    """Per-client sliding window of failed logins."""

    def __init__(self) -> None:
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, client: str, now: float) -> deque[float]:
        q = self._failures.setdefault(client, deque())
        while q and q[0] < now - FAILURE_WINDOW_S:
            q.popleft()
        return q

    def blocked(self, client: str, now: float | None = None) -> bool:
        return len(self._recent(client, now or time.time())) >= MAX_FAILURES

    def record_failure(self, client: str, now: float | None = None) -> None:
        t = now or time.time()
        self._recent(client, t).append(t)

    def reset(self, client: str) -> None:
        self._failures.pop(client, None)
