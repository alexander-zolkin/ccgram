"""Web Push for the installed web app (PWA): replies reach a closed browser.

CCGRAM-HOTFIX:webui-push. The VAPID key pair and the browser subscriptions
live in ``<CCGRAM_DIR>/webpush.json``. A push goes out for a finished agent
reply unless some browser reported the web UI visible within the last
``VISIBLE_GRACE_S`` (the open page shows its own notification then), and
never for a chat whose sound mode is "off". Sending is fire-and-forget on a
worker thread; endpoints the push service reports gone (404/410) are dropped.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VISIBLE_GRACE_S = 45.0
_SUBJECT = "https://kara.adzol.ru"
_TTL_S = 6 * 3600
_GONE = (404, 410)

_state: dict[str, Any] | None = None
_visible: dict[str, float] = {}  # client id → last "visible" report
# Web notifications are their own channel, independent of Telegram's sound
# settings: a global switch (every device) and a per-chat switch.
_web_muted = False
_web_off: set[int] = set()


def _path() -> Path:
    base = os.getenv("CCGRAM_DIR") or os.path.expanduser("~/.ccgram")
    return Path(base) / "webpush.json"


def _save() -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".webpush.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(_load(), fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _load() -> dict[str, Any]:
    global _state
    if _state is None:
        try:
            _state = json.loads(_path().read_text(encoding="utf-8"))
        except OSError, ValueError:
            _state = {}
        if not isinstance(_state, dict):
            _state = {}
        _state.setdefault("subscriptions", [])
    return _state


def _keys() -> tuple[str, str]:
    """(private key PEM, applicationServerKey) — generated on first use."""
    state = _load()
    if not state.get("private_pem"):
        # Lazy: crypto libs are only needed once push is actually used.
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid01

        vapid = Vapid01()
        vapid.generate_keys()
        state["private_pem"] = vapid.private_pem().decode()
        raw = vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        state["public_key"] = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        _save()
    return state["private_pem"], state["public_key"]


def public_key() -> str:
    return _keys()[1]


def subscribe(sub: dict[str, Any]) -> int:
    endpoint = sub.get("endpoint") if isinstance(sub, dict) else None
    keys = sub.get("keys") if isinstance(sub, dict) else None
    if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
        raise ValueError("bad subscription")
    if not isinstance(keys, dict) or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("bad subscription")
    subs = [s for s in _load()["subscriptions"] if s.get("endpoint") != endpoint]
    subs.append(
        {"endpoint": endpoint, "keys": {k: keys[k] for k in ("p256dh", "auth")}}
    )
    _load()["subscriptions"] = subs
    _save()
    return len(subs)


def unsubscribe(endpoint: str) -> None:
    state = _load()
    state["subscriptions"] = [
        s for s in state["subscriptions"] if s.get("endpoint") != endpoint
    ]
    _save()


def set_web_muted(on: bool) -> None:
    global _web_muted
    _web_muted = bool(on)


def web_muted() -> bool:
    return _web_muted


def set_chat_web(thread_id: int, on: bool) -> None:
    if on:
        _web_off.discard(int(thread_id))
    else:
        _web_off.add(int(thread_id))


def load_web_off(ids: list[int]) -> None:
    _web_off.clear()
    _web_off.update(int(i) for i in ids)


def web_off() -> list[int]:
    return sorted(_web_off)


def allowed(thread_id: int) -> bool:
    """May this chat send web notifications at all (both switches on)?"""
    return not _web_muted and int(thread_id) not in _web_off


def has_subscriptions() -> bool:
    return bool(_load()["subscriptions"])


def mark_visible(client_id: str, visible: bool) -> None:
    if visible:
        _visible[client_id[:64]] = time.monotonic()
    else:
        _visible.pop(client_id[:64], None)


def someone_watching() -> bool:
    now = time.monotonic()
    return any(now - ts < VISIBLE_GRACE_S for ts in _visible.values())


def _send_all(payload: dict[str, Any]) -> None:
    """Blocking: deliver ``payload`` to every subscription (worker thread)."""
    # Lazy: pywebpush pulls requests + cryptography.
    from pywebpush import WebPushException, webpush

    # Lazy: py_vapid comes with pywebpush.
    from py_vapid import Vapid02

    private_pem, _ = _keys()
    # webpush() takes a Vapid object, a file path or base64 DER — not PEM text.
    # Vapid02 signs as "vapid t=…,k=…" (RFC 8292), which Apple's push requires.
    vapid = Vapid02.from_pem(private_pem.encode())
    gone: list[str] = []
    data = json.dumps(payload, ensure_ascii=False)
    for sub in list(_load()["subscriptions"]):
        try:
            webpush(
                subscription_info=sub,
                data=data,
                vapid_private_key=vapid,
                vapid_claims={"sub": _SUBJECT},
                ttl=_TTL_S,
                timeout=10,
            )
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in _GONE:
                gone.append(sub["endpoint"])
            else:
                logger.warning("web push failed: %s", exc)
        except Exception:  # noqa: BLE001 — one bad endpoint must not stop the rest
            logger.exception("web push failed")
    for endpoint in gone:
        unsubscribe(endpoint)


def notify_reply(thread_id: int, title: str, text: str) -> None:
    """Queue a push for a finished reply. Never raises; never blocks."""
    try:
        if not has_subscriptions() or someone_watching() or not allowed(thread_id):
            return
        payload = {
            "title": title,
            "body": " ".join(text.split())[:180],
            "tag": f"t{thread_id}",
            "url": f"/#{thread_id}",
        }
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _send_all, payload)
    except Exception:  # noqa: BLE001 — notifications are best-effort
        logger.exception("web push scheduling failed")


def reset_for_testing() -> None:
    global _state, _web_muted
    _state = None
    _visible.clear()
    _web_muted = False
    _web_off.clear()
