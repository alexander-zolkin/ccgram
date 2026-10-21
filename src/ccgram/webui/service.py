"""Session operations behind the web UI, expressed in ccgram's own terms.

A web "session" is a bound forum topic: the list, history, send and create
paths all go through the same thread router, window state and launch service
the Telegram handlers use, so both surfaces always describe the same thing.

Telegram mirroring falls out of the existing relay: text sent here is injected
WITHOUT a Telegram-origin marker, so ``message_routing`` relays the resulting
user transcript entry into the topic just like input typed in the terminal.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from .. import window_query
from ..config import config
from ..multiplexer import multiplexer as tmux_manager
from ..multiplexer.window_ops import send_to_window
from ..providers import get_provider_for_window
from ..thread_router import thread_router
from . import transcript

if TYPE_CHECKING:
    from telegram import Bot

logger = logging.getLogger(__name__)

MAX_TEXT_LEN = 16_000
DEFAULT_HISTORY = 200
MAX_HISTORY = 1000
MAX_TRANSCRIPT_BYTES = 8 * 1024 * 1024

# Special keys the pane control strip may send (tmux key names).
ALLOWED_KEYS = frozenset(
    {"Enter", "Escape", "Up", "Down", "Left", "Right", "Tab", "BTab", "C-c"}
    | {str(n) for n in range(10)}
)


class WebUIError(Exception):
    """A user-facing failure; ``status`` is the HTTP status to return."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class Binding:
    user_id: int
    chat_id: int
    thread_id: int
    window_id: str
    web_only: bool = False


def _owner_user_id() -> int:
    if not config.allowed_users:
        raise WebUIError("no ALLOWED_USERS configured", 500)
    return min(config.allowed_users)


def _private_root() -> Path:
    override = os.environ.get("CCGRAM_PRIVATE_DIR", "").strip()
    return Path(override).expanduser() if override else Path.home() / "private"


def _is_private_cwd(cwd: str) -> bool:
    if not cwd:
        return False
    try:
        return Path(cwd).resolve().is_relative_to(_private_root().resolve())
    except OSError:
        return False


def _bindings() -> list[Binding]:
    owner = _owner_user_id()
    seen: set[tuple[int, int]] = set()
    out: list[Binding] = []
    for (
        user_id,
        chat_id,
        thread_id,
        window_id,
    ) in thread_router.iter_thread_bindings_with_chat():
        if user_id != owner or thread_id is None:
            continue
        resolved_chat = chat_id or thread_router.resolve_chat_id(user_id, thread_id)
        if thread_router.is_private_topic_chat(resolved_chat):
            continue
        key = (resolved_chat, thread_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(Binding(user_id, resolved_chat, thread_id, window_id))
    return out


def _detached(thread_id: int) -> Binding | None:
    # Lazy: registry is a leaf, but keep the import next to its only users.
    from . import registry

    entry = registry.get(thread_id)
    if entry is None:
        return None
    return Binding(_owner_user_id(), 0, thread_id, entry["window_id"], web_only=True)


def find_binding(thread_id: int) -> Binding:
    """A topic-bound session, or a web-only one (its topic was deleted)."""
    for binding in _bindings():
        if binding.thread_id == thread_id:
            if not config.webui_show_private and _is_private_cwd(_cwd_for(binding)):
                break
            return binding
    else:
        detached = _detached(thread_id)
        if detached is not None:
            return detached
    raise WebUIError("unknown session", 404)


# ── dead-window recovery data (window_states rows are pruned on death) ──────

_events_cache: dict[str, tuple[str, str, str]] = {}
_events_cache_key: tuple[float, int] | None = None


def _events_path() -> Path:
    base = os.getenv("CCGRAM_DIR") or os.path.expanduser("~/.ccgram")
    return Path(base) / "events.jsonl"


def _event_index() -> dict[str, tuple[str, str, str]]:
    """window_id → (cwd, session_id, transcript_path), last write wins.

    Rebuilt only when events.jsonl changes; one pass serves every window.
    """
    global _events_cache_key
    path = _events_path()
    try:
        st = path.stat()
    except OSError:
        return _events_cache
    key = (st.st_mtime, st.st_size)
    if key == _events_cache_key:
        return _events_cache
    index: dict[str, tuple[str, str, str]] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                wkey = ev.get("window_key") or ""
                if not wkey.startswith("ccgram:"):
                    continue
                wid = wkey.removeprefix("ccgram:")
                data = ev.get("data") or {}
                cwd, sid, tx = index.get(wid, ("", "", ""))
                cwd = data.get("cwd") or cwd
                if ev.get("session_id") and data.get("transcript_path"):
                    sid, tx = ev["session_id"], data["transcript_path"]
                index[wid] = (cwd, sid, tx)
    except OSError:
        return _events_cache
    _events_cache.clear()
    _events_cache.update(index)
    _events_cache_key = key
    return _events_cache


def _entry(binding: Binding) -> dict[str, Any]:
    # Lazy: registry is a leaf, but keep the import next to its only users.
    from . import registry

    return (registry.get(binding.thread_id) or {}) if binding.web_only else {}


def _cwd_for(binding: Binding) -> str:
    if binding.web_only:
        return _entry(binding).get("cwd", "")
    view = window_query.view_window(binding.window_id)
    if view and view.cwd:
        return view.cwd
    return _event_index().get(binding.window_id, ("", "", ""))[0]


def _transcript_for(binding: Binding) -> tuple[Path | None, str | None]:
    """(transcript path, provider name) for a live or hibernated topic."""
    if binding.web_only:
        entry = _entry(binding)
        tx = entry.get("transcript_path") or ""
        return (Path(tx) if tx else None), entry.get("provider") or None
    view = window_query.view_window(binding.window_id)
    if view and view.transcript_path:
        return Path(view.transcript_path), view.provider_name or None
    _cwd, _sid, tx = _event_index().get(binding.window_id, ("", "", ""))
    provider = window_query.get_window_provider(binding.window_id)
    return (Path(tx) if tx else None), provider


def _topic_state(chat_id: int, thread_id: int) -> str:
    # Lazy: topic_emoji imports the handler layer; keep it off module import.
    from ..handlers.status.topic_emoji import get_topic_state

    return get_topic_state(chat_id, thread_id) or "unknown"


def _session_state(binding: Binding, live: set[str]) -> str:
    state = _topic_state(binding.chat_id, binding.thread_id)
    if state == "unknown":
        # The status poller only paints topics whose state changed since the
        # daemon started; fall back to whether the window is running.
        return "idle" if binding.window_id in live else "dead"
    return state


def _topic_name(binding: Binding) -> str:
    if binding.web_only:
        return _entry(binding).get("name") or f"session {binding.thread_id}"
    # Lazy: topic_emoji imports the handler layer; keep it off module import.
    from ..handlers.status.topic_emoji import get_stored_topic_name

    return (
        get_stored_topic_name(binding.chat_id, binding.thread_id)
        or thread_router.get_display_name(binding.window_id)
        or f"topic {binding.thread_id}"
    )


# ── queries ──────────────────────────────────────────────────────────────────


async def list_sessions() -> list[dict[str, Any]]:
    # Lazy: registry is a leaf, but keep the import next to its only users.
    from . import registry

    live = {w.window_id for w in await tmux_manager.list_windows()}
    targets = _bindings()
    bound_threads = {b.thread_id for b in targets}
    for tid in registry.entries():
        if tid not in bound_threads:
            detached = _detached(tid)
            if detached is not None:
                targets.append(detached)
    sessions: list[dict[str, Any]] = []
    for b in targets:
        cwd = _cwd_for(b)
        if not config.webui_show_private and _is_private_cwd(cwd):
            continue
        tx, provider = _transcript_for(b)
        try:
            last_activity = tx.stat().st_mtime if tx else 0.0
        except OSError:
            last_activity = 0.0
        if b.web_only:
            # No topic poller paints these; liveness is all we know.
            state = "idle" if b.window_id in live else "dead"
        else:
            state = _session_state(b, live)
        sessions.append(
            {
                "thread_id": b.thread_id,
                "chat_id": b.chat_id,
                "window_id": b.window_id,
                "name": _topic_name(b),
                "state": state,
                "provider": provider or "",
                "cwd": cwd,
                "last_activity": last_activity,
                "web_only": b.web_only,
            }
        )
    sessions.sort(key=lambda s: s["last_activity"], reverse=True)
    return sessions


def _read_transcript(path: Path, provider_name: str | None) -> list[dict[str, Any]]:
    # Lazy: providers package heavy bootstrap.
    from ..providers import get_provider, registry

    provider = get_provider()  # also populates the registry
    if provider_name:
        # Unknown provider name: keep the default.
        with contextlib.suppress(Exception):
            provider = registry.get(provider_name)
    entries: list[dict[str, Any]] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        # 1M-context transcripts grow to tens of MB; the UI only shows the
        # tail, so skip to the last chunk and drop the partial first line.
        size = path.stat().st_size
        if size > MAX_TRANSCRIPT_BYTES:
            fh.seek(size - MAX_TRANSCRIPT_BYTES)
            fh.readline()
        for line in fh:
            data = provider.parse_transcript_line(line)
            if data:
                entries.append(data)
    messages, _ = provider.parse_transcript_entries(entries, {})
    return [
        {
            "role": m.role,
            "text": m.text,
            "content_type": m.content_type,
            "tool_name": m.tool_name,
            "tool_use_id": m.tool_use_id,
            "timestamp": m.timestamp,
        }
        for m in messages
    ]


async def history(thread_id: int, limit: int = DEFAULT_HISTORY) -> dict[str, Any]:
    binding = find_binding(thread_id)
    path, provider = _transcript_for(binding)
    if path is None or not path.is_file():
        return {"messages": [], "total": 0}
    messages = await asyncio.to_thread(_read_transcript, path, provider)
    limit = max(1, min(limit, MAX_HISTORY))
    return {"messages": messages[-limit:], "total": len(messages)}


async def entries(
    thread_id: int,
    *,
    limit: int = DEFAULT_HISTORY,
    before: int | None = None,
    after: int | None = None,
) -> dict[str, Any]:
    """Structured transcript items (Claude) with byte cursors for paging.

    Other providers fall back to ccgram's flattened messages, mapped onto the
    same item shapes without cursors.
    """
    binding = find_binding(thread_id)
    path, provider = _transcript_for(binding)
    if path is None or not path.is_file():
        return {"items": [], "start": 0, "end": 0, "structured": False}
    limit = max(1, min(limit, MAX_HISTORY))
    if (provider or "claude") == "claude":
        if after is not None:
            result = await asyncio.to_thread(transcript.read_after, path, after)
        else:
            result = await asyncio.to_thread(transcript.read_tail, path, limit, before)
        return {**result, "structured": True}
    if after is not None or before is not None:
        return {"items": [], "start": 0, "end": 0, "structured": False}
    messages = await asyncio.to_thread(_read_transcript, path, provider)
    items = [
        {
            "kind": "tool"
            if m["content_type"] in ("tool_use", "tool_result")
            else m["role"],
            "name": m.get("tool_name") or "",
            "id": m.get("tool_use_id") or "",
            "input": {"text": m["text"]} if m["content_type"] == "tool_use" else {},
            "text": m["text"],
            "ts": m.get("timestamp"),
        }
        for m in messages[-limit:]
        if m["content_type"] != "thinking"
    ]
    return {"items": items, "start": 0, "end": 0, "structured": False}


async def pane(thread_id: int) -> dict[str, Any]:
    # Lazy: terminal parser is only needed when a pane is inspected.
    from ..terminal_parser import extract_interactive_content

    binding = find_binding(thread_id)
    text = await tmux_manager.capture_pane(binding.window_id)
    interactive = extract_interactive_content(text) if text else None
    return {
        "alive": text is not None,
        "text": text or "",
        "interactive": (
            {"name": interactive.name, "content": interactive.content}
            if interactive
            else None
        ),
    }


async def rename(bot: Bot, thread_id: int, name: str) -> dict[str, Any]:
    """Rename a session — and its Telegram topic, keeping the status emoji."""
    # Lazy: registry is a leaf, but keep the import next to its only users.
    from . import registry

    # Lazy: topic_emoji imports the handler layer.
    from ..handlers.status.topic_emoji import (
        format_topic_name_for_mode,
        update_stored_topic_name,
    )

    name = " ".join(name.split())[:120]
    if not name:
        raise WebUIError("empty name")
    binding = find_binding(thread_id)
    if binding.web_only:
        registry.set_name(thread_id, name)
        return {"ok": True, "name": name}
    view = window_query.view_window(binding.window_id)
    mode = (view.approval_mode if view else "") or "normal"
    await bot.edit_forum_topic(
        chat_id=binding.chat_id,
        message_thread_id=binding.thread_id,
        name=format_topic_name_for_mode(name, mode),
    )
    update_stored_topic_name(binding.chat_id, binding.thread_id, name)
    return {"ok": True, "name": name}


MAX_UPLOAD = 25 * 1024 * 1024
_UPLOAD_DIR = ".ccgram-uploads"  # same folder the Telegram file handler uses
_SAFE_NAME = re.compile(r"[^\w.\-() ]+", re.UNICODE)


def save_upload(thread_id: int, filename: str, data: bytes) -> dict[str, Any]:
    """Store a browser upload in the session's cwd, like Telegram uploads."""
    if len(data) > MAX_UPLOAD:
        raise WebUIError("file too large (max 25 MB)", 413)
    binding = find_binding(thread_id)
    cwd = _cwd_for(binding)
    if not cwd or not Path(cwd).is_dir():
        raise WebUIError("session folder is gone", 409)
    base = _SAFE_NAME.sub("_", Path(filename or "file").name).strip(" .") or "file"
    folder = Path(cwd) / _UPLOAD_DIR
    folder.mkdir(exist_ok=True)
    dest = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{base[:120]}"
    if not dest.resolve().is_relative_to(folder.resolve()):
        raise WebUIError("invalid file name")
    dest.write_bytes(data)
    is_image = dest.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp")
    return {"path": f"{_UPLOAD_DIR}/{dest.name}", "name": base, "image": is_image}


# ── actions ──────────────────────────────────────────────────────────────────


async def _window_alive(window_id: str) -> bool:
    return await tmux_manager.find_window_by_id(window_id) is not None


async def _wake_web_only(binding: Binding) -> str:
    """Resume a web-only session by its own id in a fresh window."""
    # Lazy: providers package heavy bootstrap.
    from ..providers import registry as provider_registry

    # Lazy: wake/detach-only dependency.
    from ..providers import resolve_launch_command

    # Lazy: recovery pulls the handler graph; only needed to wake.
    from ..handlers.recovery.resume_dialog import dismiss_resume_dialog

    # Lazy: session_map wiring is only needed on the wake path.
    from ..session import session_manager

    # Lazy: wake/detach-only dependency.
    from ..session_map import session_map_sync

    # Lazy: wake/detach-only dependency.
    from ..window_state_store import CCGRAM_CREATED_WINDOW_ORIGIN

    # Lazy: wake/detach-only dependency.
    from . import registry

    entry = _entry(binding)
    cwd = entry.get("cwd", "")
    if not cwd or not Path(cwd).is_dir():
        raise WebUIError("session folder is gone", 409)
    provider_name = entry.get("provider") or "claude"
    approval_mode = entry.get("approval_mode") or "normal"
    try:
        provider = provider_registry.get(provider_name)
        agent_args = provider.make_launch_args(resume_id=entry["session_id"])
    except Exception as exc:  # noqa: BLE001 — surface as a user-facing error
        raise WebUIError(f"cannot resume this session: {exc}", 409) from exc
    ok, msg, _name, wid = await tmux_manager.create_window(
        cwd,
        agent_args=agent_args,
        launch_command=resolve_launch_command(
            provider_name, approval_mode=approval_mode
        ),
    )
    if not ok:
        raise WebUIError(f"could not start the session: {msg}", 502)
    # Claim the window before any await so the monitor never adopts it into
    # a fresh topic (handle_new_window checks is_web_only).
    registry.set_window(binding.thread_id, wid)
    session_manager.set_window_origin(wid, CCGRAM_CREATED_WINDOW_ORIGIN)
    session_manager.set_window_provider(wid, provider_name)
    session_manager.set_window_approval_mode(wid, approval_mode)
    if provider.capabilities.supports_hook:
        await session_map_sync.wait_for_session_map_entry(wid)
    await dismiss_resume_dialog(wid)
    return wid


async def _wake(bot: Bot, binding: Binding) -> str:
    """Resume a hibernated topic in place; return the new live window id."""
    if binding.web_only:
        return await _wake_web_only(binding)
    # Lazy: recovery pulls the whole handler graph; only needed to wake.
    from ..handlers.recovery.recovery_banner import auto_continue_from_message

    # Lazy: same recovery handler graph as above.
    from ..handlers.recovery.resume_dialog import dismiss_resume_dialog

    cwd = _cwd_for(binding)
    if not cwd or not Path(cwd).is_dir():
        raise WebUIError("session folder is gone; recover it from Telegram", 409)
    ok = await auto_continue_from_message(
        None, bot, binding.user_id, binding.thread_id, binding.window_id, cwd, ""
    )
    new_wid = thread_router.get_window_for_thread(
        binding.user_id, binding.thread_id, binding.chat_id
    )
    if not ok or not new_wid or new_wid == binding.window_id:
        raise WebUIError("could not wake the session; open it in Telegram", 409)
    await dismiss_resume_dialog(new_wid)
    return new_wid


async def send_text(bot: Bot, thread_id: int, text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise WebUIError("empty message")
    if len(text) > MAX_TEXT_LEN:
        raise WebUIError("message too long")
    binding = find_binding(thread_id)
    window_id = binding.window_id
    woke = False
    if not await _window_alive(window_id):
        window_id = await _wake(bot, binding)
        woke = True
    provider = get_provider_for_window(
        window_id, provider_name=window_query.get_window_provider(window_id)
    )
    if provider.capabilities.chat_first_command_path:
        raise WebUIError("shell sessions are Telegram-only for now", 409)
    ok, err = await send_to_window(window_id, text)
    if not ok:
        raise WebUIError(err or "send failed", 502)
    return {"ok": True, "woke": woke, "window_id": window_id}


async def send_key(thread_id: int, key: str) -> dict[str, Any]:
    if key not in ALLOWED_KEYS:
        raise WebUIError("key not allowed")
    binding = find_binding(thread_id)
    if not await _window_alive(binding.window_id):
        raise WebUIError("session is asleep", 409)
    literal = key.isdigit()
    ok = await tmux_manager.send_keys(
        binding.window_id, key, enter=False, literal=literal
    )
    if not ok:
        raise WebUIError("send failed", 502)
    return {"ok": True}


async def close_web_only(thread_id: int) -> dict[str, Any]:
    """End a web-only session: kill its window and forget it."""
    # Lazy: registry is a leaf, but keep the import next to its only users.
    from . import registry

    binding = find_binding(thread_id)
    if not binding.web_only:
        raise WebUIError("this session has a Telegram topic; delete the topic", 409)
    if await _window_alive(binding.window_id):
        await tmux_manager.kill_window(binding.window_id)
    registry.remove(thread_id)
    return {"ok": True}


class _LaunchQuery:
    """Duck-typed stand-in for the CallbackQuery ``launch_window`` edits.

    The launch service reports progress by editing the query's message; here
    that message is the bot's status post in the freshly created topic, so
    the Telegram side reads exactly as if the quick-start button was tapped.
    """

    def __init__(self, message: Any) -> None:
        self.message = message

    async def edit_message_text(self, text: str, **kwargs: Any) -> Any:
        return await self.message.edit_text(text, **kwargs)

    async def answer(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _default_topic_name() -> str:
    # Same shape as the session-topic-name hotfix: "kara MM-DD HH-MM".
    return "kara " + time.strftime("%m-%d %H-%M")


async def create_session(
    bot: Bot, text: str, name: str | None, model: str | None
) -> dict[str, Any]:
    """Open a new forum topic and launch a quick-start session bound to it."""
    # Lazy: the launch service drags in the topics handler graph.
    from ..handlers.status.topic_emoji import update_stored_topic_name

    # Lazy: quick-start defaults live in the topics handler layer.
    from ..handlers.topics.directory_browser import (
        QUICKSTART_DEFAULT_CWD,
        QUICKSTART_DEFAULT_MODE,
        QUICKSTART_DEFAULT_PROVIDER,
    )

    # Lazy: launch service pulls the topics handler graph.
    from ..handlers.topics.window_launch_service import (
        WindowLaunchRequest,
        launch_window,
    )

    # Lazy: only needed when creating a session.
    from ..last_model import recall_model

    text = text.strip()
    if len(text) > MAX_TEXT_LEN:
        raise WebUIError("message too long")
    chat_id = config.group_id
    if chat_id is None:
        raise WebUIError("CCGRAM_GROUP_ID is not configured", 500)
    if not Path(QUICKSTART_DEFAULT_CWD).is_dir():
        raise WebUIError("default directory missing", 500)
    title = (name or "").strip()[:120] or _default_topic_name()
    user_id = _owner_user_id()

    topic = await bot.create_forum_topic(chat_id=chat_id, name=title)
    thread_id = topic.message_thread_id
    # The bot's own forum_topic_created update is ignored (not an allowed
    # user), so seed the sticky name the way topic_created_handler would.
    update_stored_topic_name(chat_id, thread_id, title)
    thread_router.set_group_chat_id(user_id, thread_id, chat_id)
    status = await bot.send_message(
        chat_id=chat_id,
        message_thread_id=thread_id,
        text="🌐 Starting a session from the web…",
    )

    provider = QUICKSTART_DEFAULT_PROVIDER
    model_id = model
    if not model_id:
        remembered = recall_model(provider)
        model_id = remembered[0] if remembered else None
    context = SimpleNamespace(bot=bot, user_data={})
    # pending_text is NOT handed to launch_window: it would be injected with a
    # Telegram-origin marker and so never relayed into the topic. Send it
    # ourselves once the window is up, through the plain web path.
    result = await launch_window(
        _LaunchQuery(status),  # type: ignore[arg-type]
        context,  # type: ignore[arg-type]
        WindowLaunchRequest(
            user_id=user_id,
            thread_id=thread_id,
            provider_name=provider,
            cwd=QUICKSTART_DEFAULT_CWD,
            mode=QUICKSTART_DEFAULT_MODE,
            pending_text=None,
            model=model_id,
            chat_id=chat_id,
        ),
    )
    if not result.success or not result.window_id:
        raise WebUIError(result.error_message or "launch failed", 502)
    if text:
        ok, err = await send_to_window(result.window_id, text)
        if not ok:
            raise WebUIError(f"session started, but sending failed: {err}", 502)
    return {"thread_id": thread_id, "window_id": result.window_id, "name": title}


def _web_only_launch(model: str | None) -> tuple[str, str, str, Any]:
    """(launch command, provider name, approval mode, provider) for quick-start.

    Mirrors launch_window: fresh-session args (``--effort``), the remembered
    model, and the 1M window for claude.
    """
    # Lazy: quick-start defaults live in the topics handler layer.
    from ..handlers.topics.directory_browser import (
        QUICKSTART_DEFAULT_MODE,
        QUICKSTART_DEFAULT_PROVIDER,
    )

    # Lazy: only needed when creating a session.
    from ..last_model import recall_model

    # Lazy: keep the httpx-importing catalog off the import path.
    from ..model_catalog import with_1m_context

    # Lazy: providers package heavy bootstrap.
    from ..providers import registry as provider_registry

    # Lazy: providers package heavy bootstrap.
    from ..providers import resolve_launch_command

    provider_name, mode = QUICKSTART_DEFAULT_PROVIDER, QUICKSTART_DEFAULT_MODE
    provider = provider_registry.get(provider_name)
    args = provider.make_launch_args()
    args = args if isinstance(args, str) else ""
    model_id = model or (recall_model(provider_name) or (None,))[0]
    if model_id and provider.capabilities.supports_model_picker:
        if provider_name == "claude":
            model_id = with_1m_context(model_id)
        # Quoted: ids like ``claude-opus-5-5[1m]`` are shell globs.
        args = f"{args} --model {shlex.quote(model_id)}".strip()
    launch = resolve_launch_command(provider_name, approval_mode=mode)
    return (f"{launch} {args}" if args else launch), provider_name, mode, provider


async def create_web_only_session(
    text: str, name: str | None, model: str | None = None
) -> dict[str, Any]:
    """Launch a quick-start session with no Telegram topic at all.

    The window is registered web-only the moment it exists (no await in
    between), so the monitor never adopts it into a topic, the unbound TTL
    and the hibernator's orphan reap skip it, and the web UI owns it.
    """
    # Lazy: quick-start defaults live in the topics handler layer.
    from ..handlers.topics.directory_browser import QUICKSTART_DEFAULT_CWD

    # Lazy: launch helpers live in the topics handler layer.
    from ..handlers.topics.window_launch_service import _accept_yolo_confirmation

    # Lazy: session state wiring is only needed when creating.
    from ..session import session_manager

    # Lazy: session_map wiring is only needed when creating.
    from ..session_map import session_map_sync

    # Lazy: only needed when creating a session.
    from ..window_state_store import CCGRAM_CREATED_WINDOW_ORIGIN

    # Lazy: registry is a leaf, but keep the import next to its users.
    from . import registry

    text = text.strip()
    if len(text) > MAX_TEXT_LEN:
        raise WebUIError("message too long")
    cwd = QUICKSTART_DEFAULT_CWD
    if not Path(cwd).is_dir():
        raise WebUIError("default directory missing", 500)
    launch, provider_name, mode, provider = _web_only_launch(model)
    title = (name or "").strip()[:120] or "веб " + time.strftime("%m-%d %H-%M")

    ok, msg, _wname, wid = await tmux_manager.create_window(cwd, launch_command=launch)
    if not ok:
        raise WebUIError(f"could not start the session: {msg}", 502)
    web_id = registry.new_web_id()
    registry.detach(
        web_id,
        wid,
        name=title,
        cwd=cwd,
        provider=provider_name,
        approval_mode=mode,
        session_id="",
        transcript_path="",
    )
    session_manager.set_window_origin(wid, CCGRAM_CREATED_WINDOW_ORIGIN)
    session_manager.set_window_cwd(wid, cwd)
    session_manager.set_window_provider(wid, provider_name)
    session_manager.set_window_approval_mode(wid, mode)
    if mode == "yolo" and provider.capabilities.has_yolo_confirmation:
        await _accept_yolo_confirmation(wid)
    if provider.capabilities.supports_hook:
        await session_map_sync.wait_for_session_map_entry(wid)
    view = window_query.view_window(wid)
    if view is not None:
        registry.update(
            web_id,
            session_id=view.session_id or "",
            transcript_path=str(view.transcript_path or ""),
        )
    if text:
        sent, err = await send_to_window(wid, text)
        if not sent:
            raise WebUIError(f"session started, but sending failed: {err}", 502)
    logger.info("webui: started web-only session %d in window %s", web_id, wid)
    return {"thread_id": web_id, "window_id": wid, "name": title}
