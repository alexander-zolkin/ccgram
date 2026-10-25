"""Claude Code's own word on whether a session works.

CCGRAM-HOTFIX:claude-registry-busy — Claude Code keeps a registry entry per
running session (``<config dir>/sessions/<pid>.json`` with ``"status":
"busy" | "idle"``). Its screen shows no spinner while a reply's text streams
in, so read from the screen alone a working session went idle and active
again between every tool call — its topic renamed 🟢/🟡/🟢 over and over.
A ``busy`` entry of a live process keeps the window active.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

_TTL_S = 1.0
_cache: tuple[float, frozenset[str]] = (-_TTL_S, frozenset())


def _sessions_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "sessions"


def _alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def busy_sessions() -> frozenset[str]:
    """Session ids Claude Code marks busy, read at most once a second."""
    global _cache
    now = time.monotonic()
    if now - _cache[0] < _TTL_S:
        return _cache[1]
    busy: set[str] = set()
    try:
        files = list(_sessions_dir().glob("*.json"))
    except OSError:
        files = []
    for path in files:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(entry, dict) and entry.get("status") == "busy" and _alive(entry.get("pid")):
            sid = entry.get("sessionId")
            if isinstance(sid, str):
                busy.add(sid)
    _cache = (now, frozenset(busy))
    return _cache[1]


def is_busy(session_id: str | None) -> bool:
    return bool(session_id) and session_id in busy_sessions()


def reset_for_testing() -> None:
    global _cache
    _cache = (-_TTL_S, frozenset())
