"""Web-only sessions: topics deleted (or closed) in Telegram that live on in the browser.

When the web UI is enabled, losing a topic no longer kills its session.
The session is "detached" instead: its window keeps running, and it is
recorded here under its old thread id, which stays its web id. The topic
plumbing consults :func:`is_web_only` so it neither re-creates a topic for
the window (orphan adoption) nor reaps it (unbound TTL).

Persisted to ``<CCGRAM_DIR>/webui_sessions.json`` so a daemon restart keeps
them. Leaf module: stdlib + config only, safe to import from anywhere.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_entries: dict[int, dict[str, Any]] | None = None


def _path() -> Path:
    base = os.getenv("CCGRAM_DIR") or os.path.expanduser("~/.ccgram")
    return Path(base) / "webui_sessions.json"


def _load() -> dict[int, dict[str, Any]]:
    global _entries
    if _entries is None:
        try:
            raw = json.loads(_path().read_text(encoding="utf-8"))
            _entries = {int(k): v for k, v in raw.items() if isinstance(v, dict)}
        except OSError, ValueError:
            _entries = {}
    return _entries


def _save() -> None:
    entries = _load()
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".webui_sessions.")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({str(k): v for k, v in entries.items()}, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        logger.exception("webui registry save failed")


def reset_for_testing() -> None:
    global _entries
    _entries = None


def enabled() -> bool:
    # Lazy: config reads env at import; keep this leaf importable early.
    from ..config import config

    return bool(config.webui_port and config.webui_password)


def is_web_only(window_id: str) -> bool:
    return any(e.get("window_id") == window_id for e in _load().values())


def web_only_window_ids() -> set[str]:
    return {e["window_id"] for e in _load().values() if e.get("window_id")}


def entries() -> dict[int, dict[str, Any]]:
    return dict(_load())


def get(session_id: int) -> dict[str, Any] | None:
    return _load().get(session_id)


def detach(
    thread_id: int,
    window_id: str,
    *,
    name: str,
    cwd: str,
    provider: str,
    approval_mode: str,
    session_id: str,
    transcript_path: str,
    origin: str = "telegram",
) -> None:
    """Record a web-only session.

    ``origin`` says how it became web-only: ``telegram`` (its topic was
    deleted or closed there — the web UI asks whether to delete it too),
    ``web`` (removed from Telegram or born web-only in the browser) or
    ``kept`` (a ``telegram`` one the person chose to keep).
    """
    _load()[thread_id] = {
        "window_id": window_id,
        "name": name,
        "cwd": cwd,
        "provider": provider,
        "approval_mode": approval_mode,
        "session_id": session_id,
        "transcript_path": transcript_path,
        "detached_at": time.time(),
        "origin": origin,
    }
    _save()
    logger.info(
        "webui: topic %d detached, window %s kept web-only", thread_id, window_id
    )


def new_web_id() -> int:
    """Id for a session born web-only: negative, so it never meets a thread id."""
    return min([0, *_load().keys()]) - 1


def update(web_id: int, /, **fields: Any) -> None:
    entry = _load().get(web_id)
    if entry is not None:
        entry.update(fields)
        _save()


def set_window(session_id: int, window_id: str) -> None:
    entry = _load().get(session_id)
    if entry is not None:
        entry["window_id"] = window_id
        _save()


def set_name(session_id: int, name: str) -> None:
    entry = _load().get(session_id)
    if entry is not None:
        entry["name"] = name
        _save()


def remove(session_id: int) -> dict[str, Any] | None:
    entry = _load().pop(session_id, None)
    if entry is not None:
        _save()
    return entry


def detach_from_topic(
    user_id: int,
    chat_id: int | None,
    thread_id: int,
    window_id: str,
    origin: str = "telegram",
) -> bool:
    """Keep a topic's session web-only instead of tearing it down.

    Called by the topic-deletion / topic-close paths BEFORE they kill the
    window. Returns True when the session was kept (the caller must then not
    kill the window), False when the web UI is off or the session is private.
    Never raises.
    """
    try:
        if not enabled():
            return False
        # Lazy: window_query pulls the state ports; this leaf stays light.
        from .. import window_query

        # Lazy: wake/detach-only dependency.
        from ..thread_router import thread_router

        # Lazy: same import graph as above.
        from .service import _private_root, _event_index

        view = window_query.view_window(window_id)
        cwd, sid, tx = _event_index().get(window_id, ("", "", ""))
        if view is not None:
            cwd = view.cwd or cwd
            sid = view.session_id or sid
            tx = str(view.transcript_path) if view.transcript_path else tx
        if not cwd or not sid:
            return False
        try:
            if Path(cwd).resolve().is_relative_to(_private_root().resolve()):
                return False
        except OSError:
            return False
        # Lazy: topic_emoji imports the handler layer.
        from ..handlers.status.topic_emoji import get_stored_topic_name

        if chat_id is None:
            chat_id = thread_router.resolve_chat_id(user_id, thread_id)
        name = (
            get_stored_topic_name(chat_id, thread_id)
            or thread_router.get_display_name(window_id)
            or f"topic {thread_id}"
        )
        detach(
            thread_id,
            window_id,
            name=name,
            cwd=cwd,
            provider=(view.provider_name if view else "") or "claude",
            approval_mode=(view.approval_mode if view else "") or "normal",
            session_id=sid,
            transcript_path=tx,
            origin=origin,
        )
        return True
    except Exception:  # noqa: BLE001 — never break topic teardown
        logger.exception("webui detach failed")
        return False
