"""Topics whose session is currently driven from the web UI get silent replies.

CCGRAM-HOTFIX:webui. When the last input a topic's session received came
from the browser, the person is reading there — Telegram copies of the
replies arrive without a notification sound. The first input typed in the
topic itself flips it back to normal. In-memory only: a daemon restart
returns every topic to the default (with sound).

On top of that automatic rule, a topic can be pinned from the web UI to
always ring ("on") or never ring ("off"); ``auto`` is the rule above. Those
explicit modes are persisted by the web UI (``webui/prefs.py``) and loaded
back with ``load_modes`` at startup.
"""

from __future__ import annotations

from typing import Any

_quiet: set[tuple[int, int]] = set()
_modes: dict[int, str] = {}  # thread id → "on" | "off"; absent = "auto"
MODES = ("auto", "on", "off")


def _key(chat_id: Any, thread_id: Any) -> tuple[int, int] | None:
    try:
        return int(chat_id), int(thread_id)
    except TypeError, ValueError:
        return None


def mark_web(chat_id: Any, thread_id: Any) -> None:
    key = _key(chat_id, thread_id)
    if key is not None:
        _quiet.add(key)


def mark_telegram(chat_id: Any, thread_id: Any) -> None:
    key = _key(chat_id, thread_id)
    if key is not None:
        _quiet.discard(key)


def mode(thread_id: Any) -> str:
    try:
        return _modes.get(int(thread_id), "auto")
    except TypeError, ValueError:
        return "auto"


def set_mode(thread_id: int, new_mode: str) -> None:
    if new_mode in ("on", "off"):
        _modes[int(thread_id)] = new_mode
    else:
        _modes.pop(int(thread_id), None)


def load_modes(modes: dict[int, str]) -> None:
    _modes.clear()
    for tid, m in modes.items():
        set_mode(tid, m)


def is_quiet(chat_id: Any, thread_id: Any) -> bool:
    key = _key(chat_id, thread_id)
    if key is None:
        return False
    explicit = _modes.get(key[1])
    if explicit is not None:
        return explicit == "off"
    return key in _quiet


def apply(chat_id: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Default ``disable_notification`` for a send into a web-driven topic."""
    if is_quiet(chat_id, kwargs.get("message_thread_id")):
        kwargs.setdefault("disable_notification", True)
    return kwargs


def reset_for_testing() -> None:
    _quiet.clear()
    _modes.clear()
