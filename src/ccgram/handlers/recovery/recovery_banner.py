"""Dead-window recovery banner UX flow.

Owns the banner the user sees in three situations:
  - a tmux window died proactively (``dead`` mode),
  - the user invoked ``/restore`` (``restore`` mode),
  - the user opened the resume picker (``resume`` mode).

Public surface:
  - :class:`RecoveryBanner` / :data:`RecoveryMode`
  - :func:`render_banner`, :func:`build_recovery_keyboard`
  - :func:`_create_and_bind_window` (used by :mod:`resume_picker` to wire a
    new window after the user picks a session)
  - the per-button handlers ``_handle_back/_fresh/_continue/_resume/
    _send_empty_state/_handle_browse/_handle_cancel``

The dispatcher in :mod:`recovery_callbacks` routes button taps here. The
sibling cycle with :mod:`resume_picker` is one-way at the top level
(this module imports from the picker for ``scan_sessions_for_cwd``); the
reverse direction lives behind a lazy import inside the picker.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog
from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError

from ... import synthetic_continue, window_query
from ...providers import get_provider, get_provider_for_window, resolve_launch_command
from ...session import session_manager
from ...session_map import session_map_sync
from ...telegram_client import PTBTelegramClient
from ...thread_router import thread_router
from ...tmux_manager import send_to_window, tmux_manager
from ...window_state_store import CCGRAM_CREATED_WINDOW_ORIGIN
from ..callback_data import (
    CB_RECOVERY_BACK,
    CB_RECOVERY_BROWSE,
    CB_RECOVERY_CANCEL,
    CB_RECOVERY_CONTINUE,
    CB_RECOVERY_FRESH,
    CB_RECOVERY_RESUME,
)
from ..callback_helpers import get_thread_id
from ..messaging_pipeline.message_sender import safe_edit, safe_send
from ..status.topic_emoji import format_topic_name_for_mode, get_stored_topic_name
from ..user_state import (
    PENDING_THREAD_ID,
    PENDING_THREAD_TEXT,
    RECOVERY_SESSIONS,
    RECOVERY_WINDOW_ID,
)
from .recovery_callbacks import _clear_recovery_state
from .resume_picker import (
    _build_empty_resume_keyboard,
    _build_resume_picker_keyboard,
    scan_sessions_for_cwd,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()

RecoveryMode = Literal["dead", "restore", "resume"]


@dataclass(frozen=True)
class RecoveryBanner:
    """Inputs for the unified recovery banner.

    The banner is the dead-window notification ccgram shows in three
    situations: a window died proactively (``dead``), the user invoked
    /restore (``restore``), or the user opened the resume picker
    (``resume``). All three flow through ``render_banner`` so the keyboard,
    subtitle, and copy stay consistent across entry points.
    """

    chat_id: int
    thread_id: int
    window_id: str
    mode: RecoveryMode
    provider: str | None = None
    display: str = ""
    cwd: str = ""


def _validate_recovery_state(
    data_suffix: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[int, str] | None:
    """Validate common recovery preconditions.

    Supports two paths:
      1. Text-handler path: PENDING_THREAD_ID and RECOVERY_WINDOW_ID in user_data.
      2. Proactive notification path: no user_data state, validate via binding.

    Returns ``(thread_id, old_window_id)`` on success, or ``None`` on
    failure (caller should return early and call ``query.answer``).
    """
    thread_id = get_thread_id(update)
    if thread_id is None:
        return None

    user_id = update.effective_user.id if update.effective_user else None
    if user_id is None:
        return None

    pending_tid = (
        context.user_data.get(PENDING_THREAD_ID) if context.user_data else None
    )
    stored_wid = (
        context.user_data.get(RECOVERY_WINDOW_ID) if context.user_data else None
    )

    if pending_tid is not None:
        if thread_id != pending_tid or stored_wid != data_suffix:
            return None
    else:
        bound_wid = thread_router.get_window_for_thread(user_id, thread_id)
        if bound_wid != data_suffix:
            return None
        if context.user_data is not None:
            context.user_data[PENDING_THREAD_ID] = thread_id
            context.user_data[RECOVERY_WINDOW_ID] = data_suffix

    return thread_id, data_suffix


def render_banner(banner: RecoveryBanner) -> tuple[str, InlineKeyboardMarkup]:
    """Render the recovery banner text and inline keyboard.

    Returns the message body and a :class:`InlineKeyboardMarkup` ready to
    pass to ``safe_reply`` / ``rate_limit_send_message``. The keyboard is
    the provider-aware action keyboard from :func:`build_recovery_keyboard`
    in every mode — modes only differ in the surrounding copy so the user
    knows whether the banner appeared on its own or in response to a
    request.
    """

    keyboard = build_recovery_keyboard(banner.window_id)
    help_text = _recovery_help_text(banner.window_id)
    cwd_line = f"\n\U0001f4c2 `{banner.cwd}`" if banner.cwd else ""
    # CCGRAM-HOTFIX:ended-banner-sticky-name — prefer the sticky stored topic
    # name over the tmux/cwd-derived display, so "Session … ended." (and the
    # restore/resume banners) read the user's topic title (e.g. "Test"), not the
    # drifted window name ("workspace"). Mirrors the bind paths below.
    label = (
        get_stored_topic_name(banner.chat_id, banner.thread_id)
        or banner.display
        or banner.window_id
    )

    if banner.mode == "restore":
        title = f"\U0001f504 Restore `{label}`."
        prompt = f"Choose how to continue.\n{help_text}"
    elif banner.mode == "resume":
        title = f"⏪ Resume `{label}`."
        prompt = f"Pick a session below or use the menu.\n{help_text}"
    else:
        title = f"⚠ Session `{label}` ended."
        prompt = f"Tap a button or send a message to recover.\n{help_text}"

    text = f"{title}{cwd_line}\n\n{prompt}"
    return text, keyboard


def _recovery_help_text(window_id: str) -> str:
    """Return a one-line subtitle explaining the available recovery actions.

    Mirrors the keyboard layout in ``build_recovery_keyboard`` so users can
    read what each button does without trial and error. Buttons hidden by
    the active provider's capabilities are omitted from the subtitle too.
    """

    caps = get_provider_for_window(
        window_id, provider_name=window_query.get_window_provider(window_id)
    ).capabilities
    parts = ["Start fresh"]
    if caps.supports_continue:
        parts.append("Continue last session")
    if caps.supports_resume:
        parts.append("Resume from list")
    return " · ".join(parts)


def build_recovery_keyboard(window_id: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for dead window recovery options.

    Buttons for Continue and Resume are only shown when the active provider
    declares support for those capabilities.
    """

    caps = get_provider_for_window(
        window_id, provider_name=window_query.get_window_provider(window_id)
    ).capabilities
    options: list[InlineKeyboardButton] = [
        InlineKeyboardButton(
            "\U0001f195 Fresh",
            callback_data=f"{CB_RECOVERY_FRESH}{window_id}"[:64],
        ),
    ]
    if caps.supports_continue:
        options.append(
            InlineKeyboardButton(
                "▶ Continue",
                callback_data=f"{CB_RECOVERY_CONTINUE}{window_id}"[:64],
            )
        )
    if caps.supports_resume:
        options.append(
            InlineKeyboardButton(
                "⏪ Resume",
                callback_data=f"{CB_RECOVERY_RESUME}{window_id}"[:64],
            )
        )
    return InlineKeyboardMarkup(
        [
            options,
            [InlineKeyboardButton("✖ Cancel", callback_data=CB_RECOVERY_CANCEL)],
        ]
    )


async def _create_and_bind_window(
    query: CallbackQuery,
    user_id: int,
    thread_id: int,
    cwd: str,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    agent_args: str = "",
    success_label: str = "Session started.",
    old_window_id: str = "",
) -> bool:
    """Create a new tmux window, bind it, rename topic, forward pending text.

    Returns True on success, False on failure.
    """
    thread_router.unbind_thread(user_id, thread_id)
    # Lazy: polling_state → recovery_banner via callback_registry
    # side effects.
    # Lazy: polling.polling_state pulls heavy strategy stack; defer per-call
    from ..polling.polling_state import lifecycle_strategy

    lifecycle_strategy.clear_dead_notification(user_id, thread_id)

    if old_window_id:
        old_view = window_query.view_window(old_window_id)
        provider = get_provider_for_window(
            old_window_id, provider_name=old_view.provider_name if old_view else None
        )
        approval_mode = old_view.approval_mode if old_view else "normal"
    else:
        provider = get_provider()
        approval_mode = "normal"
    launch_command = resolve_launch_command(
        provider.capabilities.name, approval_mode=approval_mode
    )

    success, message, created_wname, created_wid = await tmux_manager.create_window(
        cwd, agent_args=agent_args, launch_command=launch_command
    )
    if not success:
        await safe_edit(query, f"❌ {message}")
        _clear_recovery_state(context.user_data)
        await query.answer("Failed")
        return False

    # CCGRAM-HOTFIX:fresh-no-dup-topic — mirror the directory flow's MC-2967
    # race-guard. The awaits below (wait_for_session_map_entry) yield the event
    # loop; without this tag SessionMonitor's poll sees the new unbound window
    # and auto-creates a DUPLICATE topic named after the tmux window
    # ("workspace-3") before bind_thread() runs. Cleared right after the bind.
    from ..topics import topic_orchestration
    topic_orchestration.register_pending_creation(created_wid)

    if provider.capabilities.supports_hook:
        await session_map_sync.wait_for_session_map_entry(created_wid)

    session_manager.set_window_origin(created_wid, CCGRAM_CREATED_WINDOW_ORIGIN)
    session_manager.set_window_provider(created_wid, provider.capabilities.name)
    session_manager.set_window_approval_mode(created_wid, approval_mode)

    thread_router.bind_thread(
        user_id, thread_id, created_wid, window_name=created_wname
    )
    # CCGRAM-HOTFIX:fresh-no-dup-topic — bind is durable; release the race-guard
    # so late SessionMonitor polls take the already-bound branch.
    topic_orchestration.clear_pending_creation(created_wid)
    chat = query.message.chat if query.message else None
    if chat and chat.type in ("group", "supergroup"):
        thread_router.set_group_chat_id(user_id, thread_id, chat.id)

    client = PTBTelegramClient(context.bot)
    # CCGRAM-HOTFIX:sticky-bind-name — keep an existing topic title on recovery bind.
    _chat_id = thread_router.resolve_chat_id(user_id, thread_id)
    _label = get_stored_topic_name(_chat_id, thread_id) or created_wname
    try:
        await client.edit_forum_topic(
            chat_id=_chat_id,
            message_thread_id=thread_id,
            name=format_topic_name_for_mode(_label, approval_mode),
        )
    except TelegramError as e:
        logger.debug("Failed to rename topic: %s", e)

    await safe_edit(query, f"✅ {message}\n\n{success_label}")

    pending_text = (
        context.user_data.get(PENDING_THREAD_TEXT) if context.user_data else None
    )
    _clear_recovery_state(context.user_data)
    if pending_text:
        send_ok, send_msg = await send_to_window(created_wid, pending_text)
        if not send_ok:
            logger.warning(
                "Failed to forward pending text to window %s (user %s): %s",
                created_wid,
                user_id,
                send_msg,
            )
            await safe_send(
                client,
                thread_router.resolve_chat_id(user_id, thread_id),
                f"❌ Failed to send pending message: {send_msg}",
                message_thread_id=thread_id,
            )
    await query.answer("Created")
    return True


def _recover_session_id_for_window(  # CCGRAM-HOTFIX:resume-own-session
    window_id: str,
) -> tuple[str, str]:
    """Recover (session_id, transcript_path) for a window from events.jsonl.

    Mirrors ``_recover_cwd_for_window``: a dead window's ``window_states`` row is
    usually pruned by the monitor before autoresume runs, but the append-only
    event log retains what the hook wrote at SessionStart. Returns the most
    recent event carrying BOTH a session id and a transcript path (so the two
    stay paired), or ("", "") if none / unreadable.
    """
    import json as _json
    import os as _os

    key = f"ccgram:{window_id}"
    base = _os.getenv("CCGRAM_DIR") or _os.path.expanduser("~/.ccgram")
    path = Path(base) / "events.jsonl"
    found_sid, found_tx = "", ""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = _json.loads(line)
                except ValueError:
                    continue
                if ev.get("window_key") != key:
                    continue
                sid = ev.get("session_id") or ""
                tx = (ev.get("data") or {}).get("transcript_path") or ""
                if sid and tx:  # paired SessionStart-style event; keep the latest
                    found_sid, found_tx = sid, tx
    except OSError:
        return "", ""
    return found_sid, found_tx


async def _session_held_by_other_live_window(  # CCGRAM-HOTFIX:resume-session-collision
    sid: str, exclude_window_id: str
) -> bool:
    """True if a LIVE window other than ``exclude_window_id`` holds session ``sid``.

    Resuming a session another live topic already owns would cross-wire the two
    (one Claude session, two topics). Used to bail autoresume to the recovery
    banner instead of bleeding. Shared by the own-session and cwd-newest paths.
    """
    if not sid:
        return False
    live_ids = {w.window_id for w in await tmux_manager.list_windows()}
    for _uid, _tid, bound_wid in thread_router.iter_thread_bindings():
        if bound_wid == exclude_window_id or bound_wid not in live_ids:
            continue
        if window_query.get_session_id_for_window(bound_wid) == sid:
            logger.warning(
                "resume: session %s already held by live window %s - refusing to "
                "avoid cross-topic hijack",
                sid,
                bound_wid,
            )
            return True
    return False


def decide_launch_args(  # CCGRAM-HOTFIX:resume-own-session
    provider,
    own_sid: str,
    own_transcript_ok: bool,
    own_contended: bool,
    candidate_sid: str,
    cwd_contended: bool,
) -> tuple[str | None, bool]:
    """Pure launch-args decision for autoresume (no I/O).

    Returns ``(agent_args, arm_synthetic)``. ``agent_args`` is None to signal
    "bail to the recovery banner" on a genuine collision. Prefers resuming this
    topic's OWN session by id (immune to same-cwd cross-wire); falls back to
    today's cwd-newest ``--continue`` when no usable own session exists.

    arm_synthetic is True only on the ``--continue`` branch: that launch makes
    the harness run the "Continue from where you left off." placeholder round.
    ``--resume <id>`` does not (the existing /resume + recovery-PICK paths never
    arm), so arming it would swallow the real first reply.
    """
    if own_sid and own_transcript_ok:
        if own_contended:
            return None, False  # CCGRAM-HOTFIX:resume-session-collision
        try:
            return provider.make_launch_args(resume_id=own_sid), False
        except ValueError:
            pass  # malformed id -> fall back to --continue
    if candidate_sid and cwd_contended:
        return None, False  # CCGRAM-HOTFIX:resume-session-collision
    return provider.make_launch_args(use_continue=True), True


async def auto_continue_from_message(  # CCGRAM-HOTFIX:autoresume
    message,
    bot,
    user_id: int,
    thread_id: int,
    old_window_id: str,
    cwd: str,
    pending_text: str,
) -> bool:
    """Message-driven variant of ``_handle_continue``.

    Resume the most recent session for ``cwd`` via ``--continue``, bind it to the
    topic, rename, and forward ``pending_text`` — so a message to a hibernated
    (dead-but-bound) topic wakes the same session with zero taps. Returns True on
    success; False (never raises) to let the caller fall back to the banner.
    """
    try:
        if not cwd or not Path(cwd).is_dir():
            return False
        candidates = await asyncio.to_thread(scan_sessions_for_cwd, cwd)
        if not candidates:
            return False

        old_view = window_query.view_window(old_window_id)
        provider = get_provider_for_window(
            old_window_id,
            provider_name=old_view.provider_name if old_view else None,
        )
        approval_mode = old_view.approval_mode if old_view else "normal"
        # Preserve the user's topic name across hibernate/wake (don't rename to cwd)
        keep_name = thread_router.get_display_name(old_window_id)
        if not keep_name or keep_name == old_window_id:
            keep_name = ""

        # CCGRAM-HOTFIX:resume-own-session — resume THIS topic's OWN session by id
        # instead of the cwd-newest session via --continue. When several topics
        # share one cwd, --continue can grab a different (still-live) topic's
        # session -> cross-topic bleed. Recover the dead window's own session id +
        # transcript from events.jsonl (its window_states row is usually pruned on
        # death). Claude only for v1; other providers keep --continue below.
        own_sid, own_tx = "", ""
        if provider.capabilities.name == "claude":
            own_sid, own_tx = await asyncio.to_thread(
                _recover_session_id_for_window, old_window_id
            )
            if not own_sid and old_view and old_view.session_id:
                own_sid = old_view.session_id
                own_tx = (
                    str(old_view.transcript_path) if old_view.transcript_path else ""
                )
        own_transcript_ok = bool(own_tx) and await asyncio.to_thread(
            Path(own_tx).is_file
        )
        own_contended = bool(own_sid) and await _session_held_by_other_live_window(
            own_sid, old_window_id
        )
        candidate_sid = candidates[0].session_id
        cwd_contended = bool(candidate_sid) and await _session_held_by_other_live_window(
            candidate_sid, old_window_id
        )

        launch_args, arm_synthetic = decide_launch_args(
            provider,
            own_sid,
            own_transcript_ok,
            own_contended,
            candidate_sid,
            cwd_contended,
        )
        if launch_args is None:
            logger.warning(
                "autoresume: session collision (own=%s candidate=%s) -> bailing "
                "to recovery banner",
                own_sid or "-",
                candidate_sid or "-",
            )
            return False

        launch_command = resolve_launch_command(
            provider.capabilities.name, approval_mode=approval_mode
        )

        success, msg, created_wname, created_wid = await tmux_manager.create_window(
            cwd, agent_args=launch_args, launch_command=launch_command
        )
        if not success:
            logger.warning("autoresume: create_window failed: %s", msg)
            return False

        # CCGRAM-HOTFIX:skip-synthetic-continue — only --continue makes the harness
        # run a "Continue from where you left off." placeholder round; arm
        # suppression on that branch only (decide_launch_args sets arm_synthetic).
        # --resume <id> does not emit it, so arming there would swallow the real
        # first reply. Armed by window id (known now); disarmed by the first real
        # user turn / tool call in message_routing.
        if arm_synthetic:
            synthetic_continue.arm(created_wid)

        if keep_name:
            await tmux_manager.rename_window(created_wid, keep_name)
        else:
            keep_name = created_wname

        if provider.capabilities.supports_hook:
            await session_map_sync.wait_for_session_map_entry(created_wid)

        session_manager.set_window_origin(created_wid, CCGRAM_CREATED_WINDOW_ORIGIN)
        session_manager.set_window_provider(created_wid, provider.capabilities.name)
        session_manager.set_window_approval_mode(created_wid, approval_mode)

        thread_router.unbind_thread(user_id, thread_id)
        thread_router.bind_thread(
            user_id, thread_id, created_wid, window_name=keep_name
        )
        chat = getattr(message, "chat", None)
        if chat is not None and chat.type in ("group", "supergroup"):
            thread_router.set_group_chat_id(user_id, thread_id, chat.id)

        client = PTBTelegramClient(bot)
        # CCGRAM-HOTFIX:sticky-bind-name — autoresume must not clobber the topic
        # title; prefer the sticky stored name over the recreated window name.
        _chat_id = thread_router.resolve_chat_id(user_id, thread_id)
        _label = get_stored_topic_name(_chat_id, thread_id) or keep_name
        try:
            await client.edit_forum_topic(
                chat_id=_chat_id,
                message_thread_id=thread_id,
                name=format_topic_name_for_mode(_label, approval_mode),
            )
        except TelegramError as e:
            logger.debug("autoresume: failed to rename topic: %s", e)

        if pending_text:
            send_ok, send_msg = await send_to_window(created_wid, pending_text)
            if not send_ok:
                logger.warning("autoresume: forward pending text failed: %s", send_msg)
        logger.info(
            "autoresume: woke thread %d in %s -> window %s",
            thread_id,
            cwd,
            created_wid,
        )
        return True
    except Exception as e:  # never break the inbound message path
        logger.warning("autoresume: failed, falling back to banner: %s", e)
        return False


def _cwd_for_window(window_id: str) -> str:
    """Return the bound cwd for ``window_id`` or empty string."""
    view = window_query.view_window(window_id)
    return view.cwd if view else ""


async def _handle_back(
    query: CallbackQuery,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_RECOVERY_BACK: return to the recovery options menu."""
    window_id = data[len(CB_RECOVERY_BACK) :]
    validated = _validate_recovery_state(window_id, update, context)
    if validated is None:
        await query.answer("Stale recovery (topic mismatch)", show_alert=True)
        return
    thread_id, _ = validated
    if query.message is None or query.message.chat is None:
        await query.answer("Chat unavailable", show_alert=True)
        return
    chat_id = query.message.chat.id
    display = thread_router.get_display_name(window_id) or window_id
    banner = RecoveryBanner(
        chat_id=chat_id,
        thread_id=thread_id,
        window_id=window_id,
        mode="restore",
        provider=window_query.get_window_provider(window_id),
        display=display,
        cwd=_cwd_for_window(window_id),
    )
    text, kb = render_banner(banner)
    await safe_edit(query, text, reply_markup=kb)
    await query.answer()


async def _handle_fresh(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_RECOVERY_FRESH: create fresh session in same directory."""
    old_wid = data[len(CB_RECOVERY_FRESH) :]
    validated = _validate_recovery_state(old_wid, update, context)
    if validated is None:
        await query.answer("Stale recovery (topic mismatch)", show_alert=True)
        return

    thread_id, _ = validated
    cwd = _cwd_for_window(old_wid)
    if not cwd or not Path(cwd).is_dir():
        await safe_edit(query, "❌ Directory no longer exists.")
        _clear_recovery_state(context.user_data)
        await query.answer("Project gone")
        return

    await _create_and_bind_window(
        query,
        user_id,
        thread_id,
        cwd,
        context,
        success_label="Fresh session started.",
        old_window_id=old_wid,
    )


async def _handle_continue(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_RECOVERY_CONTINUE: resume most recent session via --continue.

    If there are no sessions on disk for ``cwd``, ``--continue`` would fail
    silently inside the agent. Surface the empty-state UI instead so the
    user can pick another project or start fresh.
    """
    old_wid = data[len(CB_RECOVERY_CONTINUE) :]
    validated = _validate_recovery_state(old_wid, update, context)
    if validated is None:
        await query.answer("Stale recovery (topic mismatch)", show_alert=True)
        return

    thread_id, _ = validated
    cwd = _cwd_for_window(old_wid)
    if not cwd or not Path(cwd).is_dir():
        await safe_edit(query, "❌ Directory no longer exists.")
        _clear_recovery_state(context.user_data)
        await query.answer("Project gone")
        return

    if not await asyncio.to_thread(scan_sessions_for_cwd, cwd):
        await _send_empty_state(query, old_wid, cwd)
        return

    launch_args = get_provider_for_window(
        old_wid, provider_name=window_query.get_window_provider(old_wid)
    ).make_launch_args(use_continue=True)
    await _create_and_bind_window(
        query,
        user_id,
        thread_id,
        cwd,
        context,
        agent_args=launch_args,
        success_label="Continuing previous session.",
        old_window_id=old_wid,
    )


async def _handle_resume(
    query: CallbackQuery,
    _user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_RECOVERY_RESUME: show session picker for --resume."""
    old_wid = data[len(CB_RECOVERY_RESUME) :]
    validated = _validate_recovery_state(old_wid, update, context)
    if validated is None:
        await query.answer("Stale recovery (topic mismatch)", show_alert=True)
        return

    cwd = _cwd_for_window(old_wid)
    if not cwd or not Path(cwd).is_dir():
        await safe_edit(query, "❌ Directory no longer exists.")
        _clear_recovery_state(context.user_data)
        await query.answer("Project gone")
        return

    sessions = await asyncio.to_thread(scan_sessions_for_cwd, cwd)
    if not sessions:
        await _send_empty_state(query, old_wid, cwd)
        return

    if context.user_data is not None:
        context.user_data[RECOVERY_SESSIONS] = [
            {"session_id": s.session_id, "summary": s.summary, "mtime": s.mtime}
            for s in sessions
        ]

    keyboard = _build_resume_picker_keyboard(sessions, old_wid)
    await safe_edit(
        query,
        f"⏪ Select a session to resume:\n(`{cwd}`)",
        reply_markup=keyboard,
    )
    await query.answer()


async def _send_empty_state(
    query: CallbackQuery,
    window_id: str,
    cwd: str,
) -> None:
    """Edit the recovery message to the no-sessions empty-state UI.

    Replaces the legacy ``query.answer("No sessions ...", show_alert=True)``
    toast with an inline keyboard so the user has explicit next steps
    instead of being trapped on a dismissable alert.
    """

    keyboard = _build_empty_resume_keyboard(window_id)
    await safe_edit(
        query,
        f"⚠ No sessions in this folder yet.\n(`{cwd}`)",
        reply_markup=keyboard,
    )
    await query.answer()


async def _handle_browse(
    query: CallbackQuery,
    _user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_RECOVERY_BROWSE: switch to the cross-project resume picker.

    The user explicitly chose to look outside the bound cwd, so the pending
    text — which targeted the original project — is dropped before
    delegating to the /resume cross-project flow.
    """

    # Lazy: sibling cycle — resume_command imports from this package.
    from ..user_state import RESUME_SESSIONS

    # Lazy: recovery_banner ↔ resume_command cycle through the picker
    from .resume_command import _build_resume_keyboard, scan_all_sessions

    old_wid = data[len(CB_RECOVERY_BROWSE) :]
    validated = _validate_recovery_state(old_wid, update, context)
    if validated is None:
        await query.answer("Stale recovery (topic mismatch)", show_alert=True)
        return

    sessions = await asyncio.to_thread(scan_all_sessions)
    if not sessions:
        await safe_edit(query, "⚠ No past sessions found in any project.")
        _clear_recovery_state(context.user_data)
        await query.answer("Nothing to resume")
        return

    if context.user_data is not None:
        context.user_data.pop(PENDING_THREAD_TEXT, None)
        context.user_data.pop(RECOVERY_SESSIONS, None)
        context.user_data[RESUME_SESSIONS] = [
            {
                "session_id": s.session_id,
                "summary": s.summary,
                "cwd": s.cwd,
                "mtime": s.mtime,
                "msg_count": s.msg_count,
            }
            for s in sessions
        ]

    keyboard = _build_resume_keyboard(
        context.user_data[RESUME_SESSIONS] if context.user_data else [], page=0
    )
    await safe_edit(query, "⏪ Select a session to resume:", reply_markup=keyboard)
    await query.answer()


async def _handle_cancel(
    query: CallbackQuery,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_RECOVERY_CANCEL: cancel recovery."""
    # Lazy: callback_helpers ↔ recovery cycle

    thread_id = get_thread_id(update)
    if thread_id is None:
        await query.answer("Stale recovery (topic mismatch)", show_alert=True)
        return

    pending_tid = (
        context.user_data.get(PENDING_THREAD_ID) if context.user_data else None
    )
    if pending_tid is not None and thread_id != pending_tid:
        await query.answer("Stale recovery (topic mismatch)", show_alert=True)
        return

    _clear_recovery_state(context.user_data)
    await safe_edit(query, "Cancelled. Send a message to try again.")
    await query.answer("Cancelled")
