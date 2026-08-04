"""Provider and mode selection callbacks for the topic-creation flow.

Handles CB_PROV_SELECT (select provider, then show mode picker or go direct to
window creation) and CB_MODE_SELECT (select launch mode and create the window).

Both ultimately call ``launch_window`` from ``window_launch_service``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog

from ...providers import registry as provider_registry
from ...thread_router import thread_router
from ..callback_data import CB_MODE_SELECT, CB_PROV_SELECT, CB_WIZ_MODEL_PICK
from ..callback_helpers import get_thread_id
from ..messaging_pipeline.message_sender import safe_edit
from ..user_state import PENDING_MODEL_ID, PENDING_MODEL_NAME
from .directory_browser import (
    build_mode_picker,
    build_wizard_model_picker,
    clear_browse_state,
    clear_model_state,
    clear_worktree_state,
)
from .topic_creation_draft import (
    PENDING_THREAD_ID,
    PENDING_THREAD_TEXT,
    _required_selected_path,
)
from .window_launch_service import WindowLaunchRequest, launch_window

if TYPE_CHECKING:
    from telegram import CallbackQuery, Update
    from telegram.ext import ContextTypes

logger = structlog.get_logger()

# Model ids are typed into a shell via send_keys, so restrict them to safe
# characters even though they arrive from our own catalog (defence in depth).
_WIZ_MODEL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")

__all__ = [
    "_validate_provider_select",
    "_handle_provider_select",
    "_parse_mode_select",
    "_handle_mode_select",
    "_handle_wizard_model_pick",
    "_show_mode_picker_after_model",
]


async def _validate_provider_select(
    query: CallbackQuery,
    user_id: int,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    pending_thread_id: int | None,
) -> bool:
    """Validate provider select callback; returns True if request should proceed."""

    confirm_thread_id = get_thread_id(update)
    if pending_thread_id is not None and confirm_thread_id != pending_thread_id:
        # _handle_mode_select clears browse state before calling this, so
        # _check_ui_guards can no longer catch a leftover worktree flow on
        # a later message — clear it here or the CREATING re-entrancy flag
        # sticks and blocks every future worktree confirm.
        clear_worktree_state(context.user_data)
        if context.user_data is not None:
            context.user_data.pop(PENDING_THREAD_ID, None)
            context.user_data.pop(PENDING_THREAD_TEXT, None)
        await query.answer("Stale browser (topic mismatch)", show_alert=True)
        return False

    await query.answer()

    # Guard against double-click: if thread already has a window, skip
    if pending_thread_id is not None:
        existing_wid = thread_router.get_window_for_thread(user_id, pending_thread_id)
        if existing_wid is not None:
            display = thread_router.get_display_name(existing_wid)
            logger.warning(
                "Thread %d already bound to window %s (%s), ignoring duplicate provider select",
                pending_thread_id,
                existing_wid,
                display,
            )
            await safe_edit(query, f"✅ Already bound to window {display}.")
            return False

    return True


async def _handle_provider_select(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_PROV_SELECT: select provider and show mode picker.

    Providers without a YOLO flag (e.g. shell) skip the mode picker
    and go directly to window creation with approval_mode="normal".
    """
    # Lazy: providers package heavy bootstrap
    from ccgram.providers import has_yolo_mode

    provider_name = data[len(CB_PROV_SELECT) :]
    if not provider_registry.is_valid(provider_name):
        await query.answer("Unknown provider", show_alert=True)
        return

    selected_path = _required_selected_path(context)
    if selected_path is None:
        await query.answer()
        await safe_edit(query, "❌ Selection expired. Tap Cancel and retry.")
        return
    pending_thread_id: int | None = (
        context.user_data.get(PENDING_THREAD_ID) if context.user_data else None
    )

    if not await _validate_provider_select(
        query, user_id, update, context, pending_thread_id
    ):
        return

    if not has_yolo_mode(provider_name):
        # No mode picker needed — go directly to window creation
        clear_browse_state(context.user_data)
        await launch_window(
            query,
            context,
            WindowLaunchRequest(
                user_id=user_id,
                thread_id=pending_thread_id,
                provider_name=provider_name,
                cwd=selected_path,
                mode="normal",
                pending_text=(
                    context.user_data.get(PENDING_THREAD_TEXT)
                    if context.user_data
                    else None
                ),
            ),
        )
        return

    # CCGRAM-HOTFIX:model-picker — providers with a model catalog (claude, grok)
    # get a model step before the mode picker. Start from a clean slate so a
    # stale pick from an earlier attempt can't leak into this launch.
    caps = provider_registry.get(provider_name).capabilities
    if caps.supports_model_picker:
        clear_model_state(context.user_data)
        await _show_wizard_model_picker(query, provider_name)
        return

    text, keyboard = build_mode_picker(selected_path, provider_name)
    await safe_edit(query, text, reply_markup=keyboard)


async def _show_wizard_model_picker(
    query: CallbackQuery, provider_name: str, selected_id: str | None = None
) -> None:
    """Render the provider-aware model picker (mid-wizard step)."""
    # Lazy: keep the httpx-based catalog off the handlers import path.
    from ...model_catalog import list_models_for_provider

    models = await list_models_for_provider(provider_name)
    text, keyboard = build_wizard_model_picker(
        provider_name, [(m.id, m.display_name) for m in models], selected_id=selected_id
    )
    await safe_edit(query, text, reply_markup=keyboard)


async def _show_mode_picker_after_model(
    query: CallbackQuery, provider_name: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Advance from the model picker to the mode picker."""
    selected_path = _required_selected_path(context)
    if selected_path is None:
        await safe_edit(query, "❌ Selection expired. Tap Cancel and retry.")
        return
    text, keyboard = build_mode_picker(selected_path, provider_name)
    await safe_edit(query, text, reply_markup=keyboard)


async def _handle_wizard_model_pick(
    query: CallbackQuery,
    user_id: int,  # noqa: ARG001 — signature parity with other handlers
    data: str,
    update: Update,  # noqa: ARG001
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_WIZ_MODEL_PICK: store the chosen model, then show the mode picker.

    Callback data is ``wm:<provider>:<model_id>`` where an empty ``model_id``
    means "keep the provider default" (clears any pending override).
    """
    raw = data[len(CB_WIZ_MODEL_PICK) :]
    provider_name, sep, model_id = raw.partition(":")
    if not sep or not provider_registry.is_valid(provider_name):
        await query.answer("Invalid model selection", show_alert=True)
        return

    pending_tid = (
        context.user_data.get(PENDING_THREAD_ID) if context.user_data else None
    )
    if pending_tid is None:
        await query.answer("Stale prompt (flow reset)", show_alert=True)
        return

    if model_id:
        if not _WIZ_MODEL_ID_RE.fullmatch(model_id):
            await query.answer("Invalid model id", show_alert=True)
            return
        # Resolve the display name from the (cached) catalog; fall back to the id.
        # Lazy: keep the httpx-based catalog off the handlers import path.
        from ...model_catalog import list_models_for_provider

        models = await list_models_for_provider(provider_name)
        name = next((m.display_name for m in models if m.id == model_id), model_id)
        if context.user_data is not None:
            context.user_data[PENDING_MODEL_ID] = model_id
            context.user_data[PENDING_MODEL_NAME] = name
        await query.answer(f"Model: {name}")
    else:
        clear_model_state(context.user_data)
        await query.answer("Using provider default")

    await _show_mode_picker_after_model(query, provider_name, context)


def _parse_mode_select(data: str) -> tuple[str, str] | None:
    """Parse mode callback data as (provider_name, approval_mode)."""
    raw = data[len(CB_MODE_SELECT) :]
    provider_name, sep, approval_mode = raw.partition(":")
    if not sep:
        return None
    return provider_name, approval_mode.lower()


async def _handle_mode_select(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle CB_MODE_SELECT: select launch mode and create tmux window."""
    parsed = _parse_mode_select(data)
    if parsed is None:
        await query.answer("Invalid mode", show_alert=True)
        return

    provider_name, approval_mode = parsed
    if not provider_registry.is_valid(provider_name):
        await query.answer("Unknown provider", show_alert=True)
        return
    if approval_mode not in ("normal", "yolo"):
        await query.answer("Unknown mode", show_alert=True)
        return

    selected_path = _required_selected_path(context)
    if selected_path is None:
        await query.answer()
        await safe_edit(query, "❌ Selection expired. Tap Cancel and retry.")
        return
    pending_thread_id: int | None = (
        context.user_data.get(PENDING_THREAD_ID) if context.user_data else None
    )

    clear_browse_state(context.user_data)

    if not await _validate_provider_select(
        query, user_id, update, context, pending_thread_id
    ):
        return

    # CCGRAM-HOTFIX:model-picker — carry the model chosen in the wizard model
    # step (if any) into the fresh launch, then clear it so it can't leak.
    model_id = context.user_data.get(PENDING_MODEL_ID) if context.user_data else None
    if model_id:
        # Remember the pick so the next quick-start prompt defaults to it.
        from ...last_model import remember_model

        picked_name = (
            context.user_data.get(PENDING_MODEL_NAME) if context.user_data else None
        )
        remember_model(provider_name, model_id, picked_name or model_id)
    clear_model_state(context.user_data)

    await launch_window(
        query,
        context,
        WindowLaunchRequest(
            user_id=user_id,
            thread_id=pending_thread_id,
            provider_name=provider_name,
            cwd=selected_path,
            mode=approval_mode,
            pending_text=(
                context.user_data.get(PENDING_THREAD_TEXT)
                if context.user_data
                else None
            ),
            model=model_id,
        ),
    )
