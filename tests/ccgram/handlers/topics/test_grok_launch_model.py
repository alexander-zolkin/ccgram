"""Grok model selection reaches the launch command as ``--model <id>``.

Focused regression test for the CCGRAM-HOTFIX:model-picker generalization:
``window_launch_service.launch_window`` must append the picked model for any
provider whose capabilities set ``supports_model_picker`` (claude, grok), and
honour ``CCGRAM_GROK_MODEL`` as the grok default when no model was picked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccgram.handlers.topics.window_launch_service import (
    WindowLaunchRequest,
    launch_window,
)
from ccgram.handlers.user_state import PENDING_THREAD_ID


def _make_query() -> AsyncMock:
    query = AsyncMock()
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.chat.type = "supergroup"
    query.message.chat.id = -100999
    return query


def _make_context() -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = {PENDING_THREAD_ID: 42}
    ctx.bot = AsyncMock()
    ctx.bot.edit_forum_topic = AsyncMock()
    return ctx


async def _run_launch(
    request: WindowLaunchRequest,
    *,
    supports_model: bool,
    accepts_prompt: bool = False,
):
    """Drive launch_window with a fully mocked backend.

    Returns ``(create_window_mock, send_to_window_mock)``.
    """
    query = _make_query()
    context = _make_context()
    with (
        patch("ccgram.handlers.topics.window_launch_service.tmux_manager") as mock_mux,
        patch("ccgram.handlers.topics.window_launch_service.session_manager"),
        patch("ccgram.handlers.topics.window_launch_service.thread_router") as mock_tr,
        patch("ccgram.handlers.topics.window_launch_service.topic_orchestration"),
        patch("ccgram.handlers.topics.window_launch_service.user_preferences"),
        patch(
            "ccgram.handlers.topics.window_launch_service.session_map_sync"
        ) as mock_sms,
        patch(
            "ccgram.handlers.topics.window_launch_service.safe_edit",
            new_callable=AsyncMock,
        ),
        patch(
            "ccgram.handlers.topics.window_launch_service.send_to_window",
            new_callable=AsyncMock,
        ) as mock_send,
        patch(
            "ccgram.handlers.topics.window_launch_service.provider_registry"
        ) as mock_reg,
        patch("ccgram.providers.resolve_launch_command", return_value="grok"),
    ):
        mock_mux.create_window = AsyncMock(return_value=(True, "created", "win", "@5"))
        mock_mux.stamp_pane_title = AsyncMock()
        mock_mux.capabilities.native_worktrees = False
        mock_tr.get_window_for_thread.return_value = None
        mock_tr.resolve_chat_id.return_value = -100999
        mock_sms.wait_for_session_map_entry = AsyncMock()
        mock_send.return_value = (True, "")

        caps = MagicMock()
        caps.chat_first_command_path = False
        caps.has_yolo_confirmation = False
        caps.supports_hook = False
        caps.supports_model_picker = supports_model
        caps.launch_accepts_initial_prompt = accepts_prompt
        provider = MagicMock()
        provider.make_launch_args.return_value = ""
        provider.capabilities = caps
        mock_reg.get.return_value = provider

        await launch_window(query, context, request)
    return mock_mux.create_window, mock_send


def _launch_command(create_window_mock) -> str:
    return create_window_mock.call_args.kwargs["launch_command"]


@pytest.mark.asyncio
async def test_picked_model_appended(tmp_path) -> None:
    req = WindowLaunchRequest(
        user_id=100,
        thread_id=42,
        provider_name="grok",
        cwd=str(tmp_path),
        mode="normal",
        pending_text=None,
        model="grok-4.5",
    )
    cw, _send = await _run_launch(req, supports_model=True)
    assert "--model grok-4.5" in _launch_command(cw)


@pytest.mark.asyncio
async def test_no_model_no_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CCGRAM_GROK_MODEL", raising=False)
    req = WindowLaunchRequest(
        user_id=100,
        thread_id=42,
        provider_name="grok",
        cwd=str(tmp_path),
        mode="normal",
        pending_text=None,
        model=None,
    )
    cw, _send = await _run_launch(req, supports_model=True)
    assert "--model" not in _launch_command(cw)


@pytest.mark.asyncio
async def test_env_default_model_used_when_none_picked(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CCGRAM_GROK_MODEL", "grok-code-fast-1")
    req = WindowLaunchRequest(
        user_id=100,
        thread_id=42,
        provider_name="grok",
        cwd=str(tmp_path),
        mode="normal",
        pending_text=None,
        model=None,
    )
    cw, _send = await _run_launch(req, supports_model=True)
    assert "--model grok-code-fast-1" in _launch_command(cw)


@pytest.mark.asyncio
async def test_initial_prompt_passed_as_launch_arg(tmp_path) -> None:
    """Grok's first message is appended to the launch command, not typed in."""
    req = WindowLaunchRequest(
        user_id=100,
        thread_id=42,
        provider_name="grok",
        cwd=str(tmp_path),
        mode="yolo",
        pending_text="Привет! Как тебя завут?",
        model="grok-4.5",
    )
    cw, send = await _run_launch(req, supports_model=True, accepts_prompt=True)
    cmd = _launch_command(cw)
    assert "Привет! Как тебя завут?" in cmd
    assert "--model grok-4.5" in cmd
    # Delivered via launch arg → must NOT also be typed into the pane.
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_initial_prompt_arg_when_provider_opts_out(tmp_path) -> None:
    """Providers without the capability keep the keystroke-delivery path."""
    req = WindowLaunchRequest(
        user_id=100,
        thread_id=42,
        provider_name="grok",
        cwd=str(tmp_path),
        mode="yolo",
        pending_text="hello there",
        model=None,
    )
    cw, send = await _run_launch(req, supports_model=True, accepts_prompt=False)
    assert "hello there" not in _launch_command(cw)
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_private_prefixes_grok_home(tmp_path, monkeypatch) -> None:
    """A private grok launch prefixes GROK_HOME to isolate the transcript."""
    from ccgram.handlers.topics import window_launch_service as wls

    monkeypatch.setattr(
        wls,
        "ensure_grok_private_home",
        lambda: tmp_path / "priv" / ".grok-home",
        raising=False,
    )
    # ensure_grok_private_home is imported lazily inside launch_window; patch the
    # source module so the lazy import picks up the stub.
    from ccgram.providers import grok_private

    monkeypatch.setattr(
        grok_private,
        "ensure_grok_private_home",
        lambda: tmp_path / "priv" / ".grok-home",
    )
    req = WindowLaunchRequest(
        user_id=100,
        thread_id=42,
        provider_name="grok",
        cwd=str(tmp_path / "priv" / "grok-x"),
        mode="yolo",
        pending_text=None,
        model="grok-4.5",
        private=True,
    )
    cw, _send = await _run_launch(req, supports_model=True, accepts_prompt=True)
    cmd = _launch_command(cw)
    assert cmd.startswith("GROK_HOME=")
    assert ".grok-home" in cmd
    assert "--model grok-4.5" in cmd


@pytest.mark.asyncio
async def test_non_private_has_no_grok_home_prefix(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CCGRAM_GROK_MODEL", raising=False)
    req = WindowLaunchRequest(
        user_id=100,
        thread_id=42,
        provider_name="grok",
        cwd=str(tmp_path),
        mode="yolo",
        pending_text=None,
        model="grok-4.5",
        private=False,
    )
    cw, _send = await _run_launch(req, supports_model=True)
    assert not _launch_command(cw).startswith("GROK_HOME=")
