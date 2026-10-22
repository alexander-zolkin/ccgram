"""Web UI extras: notify-on-done, live status, commands, voice, folders, modes.

CCGRAM-HOTFIX:webui-extras. Kept out of ``service.py`` so the core web
service stays small; everything here reads ccgram's existing in-memory state
(poller cache, task state, hook events) instead of re-deriving it.

Notification mode ("notify_mode", persisted in webui_prefs.json):

- ``each`` — ccgram's default: every Telegram message rings (subject to the
  per-topic sound modes) and Web Push fires on every finished reply.
- ``done`` — a turn is one notification. Every Telegram message of the turn
  goes out silently; when the agent stops (Stop hook) the topic gets a single
  ringing "✅ Готово" ping (the previous ping is deleted, so they never pile
  up) and Web Push delivers the turn's last reply once. A permission request
  rings the same way — it needs an answer, so it is never deferred.
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
PROBE_EVERY_S = 600.0
BROWSE_MAX_DIRS = 400

_notify_mode = "each"
_last_ping: dict[tuple[int, int], int] = {}  # (chat, thread) → ping message id
_last_reply: dict[int, tuple[str, str]] = {}  # thread → (title, text), "done" mode
_probed_at: dict[int, float] = {}


# ── notification mode ────────────────────────────────────────────────────────


def notify_mode() -> str:
    return _notify_mode


def set_notify_mode(mode: str) -> None:
    global _notify_mode
    _notify_mode = mode if mode in NOTIFY_MODES else "each"
    quiet_threads.set_turn_quiet(_notify_mode == "done")


def remember_reply(thread_id: int, title: str, text: str) -> None:
    """ "done" mode: keep the newest reply; it is pushed when the turn ends."""
    _last_reply[thread_id] = (title, text)


def _targets(window_id: str) -> list[tuple[int | None, int]]:
    """(chat id, thread id) for every topic / web chat showing ``window_id``.

    Web-only chats have no chat id (None) — they only get Web Push.
    """
    # Lazy: routing + registry are only needed once a hook fires.
    from ..thread_router import thread_router
    from . import registry

    out: list[tuple[int | None, int]] = []
    for user_id, thread_id, wid in thread_router.iter_thread_bindings():
        if wid == window_id:
            out.append((thread_router.resolve_chat_id(user_id, thread_id), thread_id))
    for sid, entry in registry.entries().items():
        if entry.get("window_id") == window_id and all(t != sid for _, t in out):
            out.append((None, sid))
    return out


def _title(chat_id: int | None, thread_id: int) -> str:
    # Lazy: topic names live in the handler layer.
    from ..handlers.status.topic_emoji import get_stored_topic_name
    from . import registry

    if chat_id:
        return get_stored_topic_name(chat_id, thread_id) or "Кара"
    return (registry.get(thread_id) or {}).get("name") or "Кара"


async def _ping(
    client: TelegramClient, chat_id: int, thread_id: int, text: str
) -> None:
    """One ringing message per topic: the previous ping is deleted first."""
    # Lazy: the send path pulls the messaging pipeline.
    from ..handlers.messaging_pipeline.message_sender import rate_limit_send_message

    old = _last_ping.pop((chat_id, thread_id), None)
    if old:
        # Already gone / too old to delete: nothing to clean up.
        with contextlib.suppress(Exception):
            await client.delete_message(chat_id, old)
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
        # Lazy: push pulls crypto libs on first use.
        from . import push

        for chat_id, thread_id in _targets(window_id):
            title, text = _last_reply.pop(thread_id, ("", ""))
            tg_ok = quiet_threads.mode(thread_id) != "off"
            if chat_id and tg_ok and not quiet_threads.all_muted():
                await _ping(client, chat_id, thread_id, "✅ Готово")
            push.notify_reply(
                thread_id, title or _title(chat_id, thread_id), text or "Готово"
            )
    except Exception:  # noqa: BLE001 — a hook must never break the bot
        logger.exception("webui on_stop failed")


async def on_attention(window_id: str, header: str, client: TelegramClient) -> None:
    """Notification hook that needs an answer (permission / plan). Never raises."""
    try:
        # Lazy: push pulls crypto libs on first use.
        from . import push

        for chat_id, thread_id in _targets(window_id):
            tg_ok = quiet_threads.mode(thread_id) != "off"
            if (
                _notify_mode == "done"
                and chat_id
                and tg_ok
                and not quiet_threads.all_muted()
            ):
                await _ping(client, chat_id, thread_id, f"⏳ {header}")
            push.notify_reply(thread_id, _title(chat_id, thread_id), f"⏳ {header}")
    except Exception:  # noqa: BLE001 — a hook must never break the bot
        logger.exception("webui on_attention failed")


# ── deleted-topic probe ──────────────────────────────────────────────────────


async def probe_topic(bot: Any, thread_id: int) -> dict[str, Any]:
    """Check (at most every 10 min per topic) that a chat's topic still exists.

    Telegram tells bots nothing when a topic is deleted; only a send fails.
    Opening a chat in the web runs ccgram's silent send+delete probe; a gone
    topic is detached exactly like the poller does it (session kept web-only).
    """
    # Lazy: probe + teardown live in the topics handler layer.
    from ..handlers.topics.topic_lifecycle import _unbind_deleted_topic
    from ..handlers.topics.topic_probe import probe_topic_exists
    from ..telegram_client import PTBTelegramClient
    from .service import _owner_user_id, find_binding

    binding = find_binding(thread_id)
    if binding.web_only or not binding.chat_id:
        return {"exists": None}
    now = time.monotonic()
    if now - _probed_at.get(thread_id, -PROBE_EVERY_S) < PROBE_EVERY_S:
        return {"exists": None, "cached": True}
    _probed_at[thread_id] = now
    client = PTBTelegramClient(bot)
    exists = await probe_topic_exists(client, binding.chat_id, thread_id)
    if exists is False:
        logger.info("webui probe: topic %d is gone, detaching", thread_id)
        await _unbind_deleted_topic(
            client, _owner_user_id(), binding.chat_id, thread_id, binding.window_id
        )
    return {"exists": exists}


# ── live status / subagents / todos / mode ───────────────────────────────────


async def pane_status(window_id: str) -> str | None:
    """The status block read straight off a pane no topic poller watches.

    None when idle: Claude Code leaves "Baked for 19s · done" under a finished
    turn, so only a running "Verbing…" spinner line counts.
    """
    # Lazy: multiplexer + terminal parser.
    from ..multiplexer import multiplexer as tmux_manager
    from ..terminal_parser import extract_interactive_content, parse_status_block

    text = await tmux_manager.capture_pane(window_id)
    if not text or extract_interactive_content(text):
        return None
    block = parse_status_block(text)
    return block if block and "…" in block.split("\n", 1)[0] else None


async def live(thread_id: int) -> dict[str, Any]:
    """What the agent is doing right now, from ccgram's poller + hook state."""
    # Lazy: polling + task state pull the handler graph.
    from ..claude_task_state import claude_task_state, get_subagent_names
    from ..handlers.polling.polling_state import terminal_poll_state
    from . import transcript
    from .service import _transcript_for, find_binding

    binding = find_binding(thread_id)
    wid = binding.window_id
    tx, _provider = _transcript_for(binding)
    turn = await asyncio.to_thread(transcript.current_turn, tx) if tx else None
    if turn:
        turn["now"] = time.time()  # the browser's clock may be off
    status = progress = raw = None
    ws = terminal_poll_state.peek_state(wid)
    result = ws.last_pyte_result if ws else None
    if result is not None and not result.is_interactive and result.raw_text:
        raw = result.raw_text
    elif binding.web_only:
        raw = await pane_status(wid)
    if raw:
        head, _, rest = raw.partition("\n")
        status = head.strip()
        progress = [line.strip() for line in rest.splitlines() if line.strip()]
    todos = []
    snap = claude_task_state.get_snapshot(wid)
    if snap is not None:
        todos = [
            {"text": it.subject or it.active_form or "", "status": it.status}
            for it in snap.items
        ]
    return {
        "status": status,
        "progress": progress or [],
        "subagents": list(get_subagent_names(wid)),
        "todos": todos,
        "wait": claude_task_state.get_wait_header(wid),
        "turn": turn,
    }


async def approval_mode(thread_id: int, cycle: bool = False) -> dict[str, Any]:
    """Claude Code's own mode (Def / Edit / Plan / Full); ``cycle`` = Shift+Tab."""
    # Lazy: provider registry + multiplexer.
    from .. import window_query
    from ..multiplexer import multiplexer as tmux_manager
    from ..providers import get_provider_for_window
    from .service import find_binding

    wid = find_binding(thread_id).window_id
    provider = get_provider_for_window(
        wid, provider_name=window_query.get_window_provider(wid)
    )
    scrape = getattr(provider, "scrape_current_mode", None)
    if cycle:
        await tmux_manager.send_keys(wid, "BTab", enter=False, literal=False)
        await asyncio.sleep(0.35)
    mode = await scrape(wid) if scrape else None
    return {"mode": mode, "launch": window_query.get_approval_mode(wid)}


async def terminal(thread_id: int) -> dict[str, Any]:
    """The pane with ANSI colours, for the in-browser terminal view."""
    # Lazy: multiplexer.
    from ..multiplexer import multiplexer as tmux_manager
    from .service import find_binding

    wid = find_binding(thread_id).window_id
    text = await tmux_manager.capture_pane(wid, with_ansi=True)
    return {"alive": text is not None, "ansi": text or ""}


# ── slash commands ───────────────────────────────────────────────────────────


def commands(thread_id: int | None, provider_name: str = "claude") -> dict[str, Any]:
    """Slash commands + skills the agent understands, for "/" autocomplete."""
    # Lazy: discovery walks the Claude config dir (cached 60 s by the catalog).
    from .. import window_query
    from ..cc_commands import discover_provider_commands
    from ..providers import get_provider_for_window
    from ..providers import registry as provider_registry
    from .service import find_binding

    if thread_id is not None:
        wid = find_binding(thread_id).window_id
        provider = get_provider_for_window(
            wid, provider_name=window_query.get_window_provider(wid)
        )
    else:
        provider = provider_registry.get(provider_name)
    found = list(discover_provider_commands(provider))
    if provider.capabilities.name == "claude":
        found += _user_skills({c.name.lstrip("/") for c in found})
    return {
        "commands": [
            {
                "name": "/" + c.name.lstrip("/"),
                "description": (c.description or "").lstrip("↗ ").strip(),
                "source": c.source,
            }
            for c in found
        ]
    }


def _user_skills(known: set[str]) -> list[Any]:
    """Top-level ~/.claude/skills Claude Code itself offers as /name.

    ccgram's catalog only lists skills marked ``user-invocable: true``; Claude
    Code treats every skill as invocable unless it says ``false``.
    """
    # Lazy: frontmatter parser + config.
    from types import SimpleNamespace

    from ..command_catalog import parse_frontmatter
    from ..config import config

    out: list[Any] = []
    root = Path(config.claude_config_dir) / "skills"
    if not root.is_dir():
        return out
    for skill in sorted(root.iterdir()):
        md = skill / "SKILL.md"
        if skill.name.startswith(".") or not md.is_file():
            continue
        fm = parse_frontmatter(md)
        name = fm.get("name", skill.name)
        if fm.get("user-invocable", "").lower() == "false" or name in known:
            continue
        desc = " ".join(fm.get("description", "").split())
        out.append(SimpleNamespace(name=name, description=desc[:200], source="skill"))
    return out


# ── voice ────────────────────────────────────────────────────────────────────


async def transcribe(data: bytes, filename: str) -> dict[str, Any]:
    """Speech to text through ccgram's configured Whisper provider."""
    # Lazy: httpx transcriber.
    from ..whisper import get_transcriber
    from .service import WebUIError

    try:
        transcriber = get_transcriber()
    except ValueError as exc:
        raise WebUIError(str(exc), 503) from exc
    if transcriber is None:
        raise WebUIError("speech-to-text is not configured", 503)
    result = await transcriber.transcribe(data, filename)
    return {"text": (result.text or "").strip()}


# ── folders ──────────────────────────────────────────────────────────────────


def _home() -> Path:
    return Path(os.path.expanduser("~")).resolve()


def browse(path: str | None) -> dict[str, Any]:
    """Sub-folders of ``path`` (inside the home folder, private root hidden)."""
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
    """A launch folder from the web: inside home, existing, not private."""
    if not path:
        return None
    browse(path)  # raises on anything not allowed
    return str(Path(path).expanduser().resolve())


async def make_worktree(repo: str, title: str) -> str:
    """New git worktree next to ``repo`` (ccgram's naming); returns its path."""
    # Lazy: worktree helpers shell out to git.
    from ..handlers.topics import worktree as wt
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


# ── resumable sessions ───────────────────────────────────────────────────────


async def resumable(cwd: str | None) -> dict[str, Any]:
    """Earlier Claude sessions of a folder that can be continued."""
    # Lazy: resume picker scans transcripts.
    from ..handlers.recovery.resume_picker import scan_sessions_for_cwd
    from ..handlers.topics.directory_browser import QUICKSTART_DEFAULT_CWD

    folder = valid_cwd(cwd) or QUICKSTART_DEFAULT_CWD
    found = await asyncio.to_thread(scan_sessions_for_cwd, folder, "claude")
    return {
        "cwd": folder,
        "sessions": [
            {"id": e.session_id, "summary": e.summary, "mtime": e.mtime} for e in found
        ],
    }


def reset_for_testing() -> None:
    _last_ping.clear()
    _last_reply.clear()
    _probed_at.clear()
    set_notify_mode("each")
