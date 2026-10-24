"""Telegram side of the hub API: notify-on-done, topic probe, launch folders.

CCGRAM-HOTFIX:webui-extras. Kept out of ``service.py`` so the core service
stays small; everything here reads ccgram's existing in-memory state.

Notification mode ("notify_mode", persisted in webui_prefs.json):

- ``done`` (default) — a turn is one notification. Every Telegram message of
  the turn goes out silently except the reply that ends it (the transcript's
  ``stop_reason`` marks it — see ``quiet_threads.final_reply``), which rings
  as usual. A permission request needs an answer, so it is never deferred:
  the topic gets a ringing "⏳ …" ping, deleted again when the turn ends.
- ``each`` — ccgram's own behaviour: every Telegram message rings (subject to
  the per-topic sound modes).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import quiet_threads

if TYPE_CHECKING:
    from ..telegram_client import TelegramClient

logger = logging.getLogger(__name__)

NOTIFY_MODES = ("each", "done")
DEFAULT_NOTIFY_MODE = "done"
PROBE_EVERY_S = 600.0
BROWSE_MAX_DIRS = 400

_notify_mode = DEFAULT_NOTIFY_MODE
_last_ping: dict[tuple[int, int], int] = {}  # (chat, thread) → ping message id
_probed_at: dict[int, float] = {}


# ── notification mode ────────────────────────────────────────────────────────


def notify_mode() -> str:
    return _notify_mode


def set_notify_mode(mode: str) -> None:
    global _notify_mode
    _notify_mode = mode if mode in NOTIFY_MODES else DEFAULT_NOTIFY_MODE
    quiet_threads.set_turn_quiet(_notify_mode == "done")


def _targets(window_id: str) -> list[tuple[int, int]]:
    """(chat id, thread id) for every topic showing ``window_id``."""
    # Lazy: routing is only needed once a hook fires.
    from ..thread_router import thread_router

    out: list[tuple[int, int]] = []
    for user_id, thread_id, wid in thread_router.iter_thread_bindings():
        if wid == window_id:
            chat_id = thread_router.resolve_chat_id(user_id, thread_id)
            if chat_id:
                out.append((chat_id, thread_id))
    return out


async def _drop_ping(client: TelegramClient, chat_id: int, thread_id: int) -> None:
    old = _last_ping.pop((chat_id, thread_id), None)
    if old:
        # Already gone / too old to delete: nothing to clean up.
        with contextlib.suppress(Exception):
            await client.delete_message(chat_id, old)


async def _ping(
    client: TelegramClient, chat_id: int, thread_id: int, text: str
) -> None:
    """One ringing message per topic: the previous ping is deleted first."""
    # Lazy: the send path pulls the messaging pipeline.
    from ..handlers.messaging_pipeline.message_sender import rate_limit_send_message

    await _drop_ping(client, chat_id, thread_id)
    msg = await rate_limit_send_message(
        client, chat_id, text, message_thread_id=thread_id, disable_notification=False
    )
    if msg is not None:
        _last_ping[(chat_id, thread_id)] = msg.message_id


async def on_stop(window_id: str, client: TelegramClient) -> None:
    """Stop hook (turn finished). Never raises."""
    try:
        if _notify_mode != "done":
            return
        for chat_id, thread_id in _targets(window_id):
            # The final reply itself rang; a "⏳" ping is answered by now.
            await _drop_ping(client, chat_id, thread_id)
    except Exception:  # noqa: BLE001 — a hook must never break the bot
        logger.exception("webui on_stop failed")


async def on_attention(window_id: str, header: str, client: TelegramClient) -> None:
    """Notification hook that needs an answer (permission / plan). Never raises."""
    try:
        if _notify_mode != "done" or quiet_threads.all_muted():
            return
        for chat_id, thread_id in _targets(window_id):
            if quiet_threads.mode(thread_id) != "off":
                await _ping(client, chat_id, thread_id, f"⏳ {header}")
    except Exception:  # noqa: BLE001 — a hook must never break the bot
        logger.exception("webui on_attention failed")


# ── deleted-topic probe ──────────────────────────────────────────────────────


async def probe_topic(bot: Any, thread_id: int) -> dict[str, Any]:
    """Check (at most every 10 min per topic) that a chat's topic still exists.

    Telegram tells bots nothing when a topic is deleted; only a send fails.
    Opening a chat in the hub runs ccgram's silent send+delete probe; a gone
    topic is detached exactly like the poller does it (session kept web-only).
    """
    # Lazy: probe + teardown live in the topics handler layer.
    from ..handlers.topics.topic_lifecycle import _unbind_deleted_topic
    from ..handlers.topics.topic_probe import probe_topic_exists_by_send
    from ..telegram_client import PTBTelegramClient
    from .service import _adapter, _owner_user_id, find_binding

    binding = find_binding(thread_id)
    if binding.web_only or not binding.chat_id:
        return {"exists": None}
    now = time.monotonic()
    if now - _probed_at.get(thread_id, -PROBE_EVERY_S) < PROBE_EVERY_S:
        return {"exists": None, "cached": True}
    _probed_at[thread_id] = now
    client = PTBTelegramClient(bot)
    exists = await probe_topic_exists_by_send(client, binding.chat_id, thread_id)
    if exists is False:
        logger.info("webui probe: topic %d is gone, detaching", thread_id)
        await _unbind_deleted_topic(
            client,
            _owner_user_id(),
            binding.chat_id,
            thread_id,
            binding.window_id,
            _adapter(),
        )
    return {"exists": exists}


# ── launch folders ───────────────────────────────────────────────────────────


def _home() -> Path:
    return Path(os.path.expanduser("~")).resolve()


def browse(path: str | None) -> dict[str, Any]:
    """Sub-folders of ``path`` (inside the home folder, private root hidden).

    Only :func:`valid_cwd` calls it now: its checks are what a launch folder
    has to pass.
    """
    # Lazy: service owns the private-root rule.
    from .service import WebUIError, _is_private_cwd

    home = _home()
    target = Path(path).expanduser().resolve() if path else home
    if target != home and home not in target.parents:
        raise WebUIError("outside the home folder", 403)
    if not target.is_dir() or _is_private_cwd(str(target)):
        raise WebUIError("folder not found", 404)
    dirs = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if child.name.startswith(".") or not child.is_dir():
                continue
            if _is_private_cwd(str(child)):
                continue
            dirs.append({"name": child.name, "git": (child / ".git").exists()})
            if len(dirs) >= BROWSE_MAX_DIRS:
                break
    except PermissionError as exc:
        raise WebUIError("no access", 403) from exc
    return {
        "path": str(target),
        "parent": str(target.parent) if target != home else None,
        "git": (target / ".git").exists(),
        "dirs": dirs,
    }


def valid_cwd(path: str | None) -> str | None:
    """A launch folder from the hub: inside home, existing, not private."""
    if not path:
        return None
    browse(path)  # raises on anything not allowed
    return str(Path(path).expanduser().resolve())


async def make_worktree(repo: str, title: str) -> str:
    """New git worktree next to ``repo`` (ccgram's naming); returns its path."""
    # Lazy: worktree helpers shell out to git.
    from .. import worktrees as wt
    from .service import WebUIError

    repo_path = valid_cwd(repo)
    if not repo_path:
        raise WebUIError("no repository", 400)
    elig = await asyncio.to_thread(wt.check_worktree_eligibility, Path(repo_path))
    if not elig.eligible or not elig.repo_path:
        raise WebUIError(elig.reason or "not a git repository", 400)
    branch = wt.suggest_branch_name(title or "web", elig.repo_path)
    path = wt.worktree_path_for(elig.repo_path, wt.slug_for_path(branch))
    try:
        await asyncio.to_thread(wt.create_worktree, elig.repo_path, branch, path)
    except wt.WorktreeError as exc:
        raise WebUIError(str(exc), 400) from exc
    return str(path)


def reset_for_testing() -> None:
    _last_ping.clear()
    _probed_at.clear()
    set_notify_mode(DEFAULT_NOTIFY_MODE)
