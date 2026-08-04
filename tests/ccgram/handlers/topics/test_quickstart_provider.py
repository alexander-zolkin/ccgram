"""Quick-start prompt provider+model selection (the 'Use default settings?' screen)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccgram.handlers.topics import directory_callbacks as dc
from ccgram.handlers.topics.directory_browser import (
    build_quickstart_prompt,
    build_quickstart_provider_picker,
)
from ccgram.handlers.callback_data import CB_DEFAULTS_PROVIDER, CB_PROVIDER_PICK
from ccgram.handlers.user_state import (
    PENDING_MODEL_ID,
    PENDING_MODEL_NAME,
    PENDING_PROVIDER,
    PENDING_THREAD_ID,
)
from ccgram.providers import _ensure_registered


@pytest.fixture(autouse=True)
def _register():
    _ensure_registered()


def _query() -> AsyncMock:
    q = AsyncMock()
    q.answer = AsyncMock()
    return q


def _context(user_data: dict) -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = user_data
    return ctx


# ── Builders ──────────────────────────────────────────────────────────────


def test_prompt_has_provider_button_always() -> None:
    _text, kb = build_quickstart_prompt("claude", "opus[1m]")
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert CB_DEFAULTS_PROVIDER in callbacks


def test_prompt_hides_model_button_without_model_label() -> None:
    _text, kb = build_quickstart_prompt("codex", None)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert not any("Model" in label for label in labels)
    assert any("Provider" in label for label in labels)


def test_prompt_shows_provider_in_text() -> None:
    text, _kb = build_quickstart_prompt("grok", "grok-4.5")
    assert "Grok" in text
    assert "grok-4.5" in text


def test_provider_picker_marks_selection() -> None:
    _text, kb = build_quickstart_provider_picker(
        ["claude", "codex", "grok"], selected="grok"
    )
    rows = [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]
    grok_btn = next(t for t, cb in rows if cb == f"{CB_PROVIDER_PICK}grok")
    assert grok_btn.startswith("✅")
    assert any(cb == f"{CB_PROVIDER_PICK}codex" for _t, cb in rows)


# ── Helpers ───────────────────────────────────────────────────────────────


def test_current_provider_defaults_to_claude() -> None:
    assert dc._current_provider(_context({})) == "claude"


def test_current_provider_reads_pending() -> None:
    assert dc._current_provider(_context({PENDING_PROVIDER: "grok"})) == "grok"


def test_provider_supports_model_picker() -> None:
    assert dc._provider_supports_model_picker("claude") is True
    assert dc._provider_supports_model_picker("grok") is True
    assert dc._provider_supports_model_picker("codex") is False


# ── Handlers ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_pick_sets_provider_and_clears_model() -> None:
    user_data = {
        PENDING_THREAD_ID: 42,
        PENDING_MODEL_ID: "opus[1m]",
        PENDING_MODEL_NAME: "Opus",
    }
    ctx = _context(user_data)
    query = _query()
    with (
        patch.object(dc, "safe_edit", new_callable=AsyncMock),
        patch(
            "ccgram.model_catalog.default_model_label_for_provider",
            return_value="grok-4.5",
        ),
    ):
        await dc._handle_provider_pick(query, 100, "qs:pp:grok", MagicMock(), ctx)
    assert user_data[PENDING_PROVIDER] == "grok"
    # Model is provider-specific → dropped when the provider changes.
    assert PENDING_MODEL_ID not in user_data
    assert PENDING_MODEL_NAME not in user_data


@pytest.mark.asyncio
async def test_provider_pick_rejects_unknown() -> None:
    user_data = {PENDING_THREAD_ID: 42}
    ctx = _context(user_data)
    query = _query()
    with patch.object(dc, "safe_edit", new_callable=AsyncMock):
        await dc._handle_provider_pick(query, 100, "qs:pp:bogus", MagicMock(), ctx)
    assert PENDING_PROVIDER not in user_data
    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_defaults_yes_launches_with_chosen_provider(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(dc, "QUICKSTART_DEFAULT_CWD", str(tmp_path))
    user_data = {
        PENDING_THREAD_ID: 42,
        PENDING_PROVIDER: "grok",
        PENDING_MODEL_ID: "grok-4.5",
        PENDING_MODEL_NAME: "Grok 4.5",
    }
    ctx = _context(user_data)
    query = _query()
    with (
        patch.object(dc, "safe_edit", new_callable=AsyncMock),
        patch.object(dc, "_validate_provider_select", new=AsyncMock(return_value=True)),
        patch.object(dc, "launch_window", new_callable=AsyncMock) as mock_launch,
    ):
        await dc._handle_defaults_yes(query, 100, MagicMock(), ctx)
    mock_launch.assert_awaited_once()
    request = mock_launch.await_args.args[2]
    assert request.provider_name == "grok"
    assert request.model == "grok-4.5"


@pytest.mark.asyncio
async def test_defaults_yes_drops_model_for_non_picker_provider(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(dc, "QUICKSTART_DEFAULT_CWD", str(tmp_path))
    # Codex has no model picker → a stray model id must not reach the launch.
    user_data = {
        PENDING_THREAD_ID: 42,
        PENDING_PROVIDER: "codex",
        PENDING_MODEL_ID: "stale",
    }
    ctx = _context(user_data)
    query = _query()
    with (
        patch.object(dc, "safe_edit", new_callable=AsyncMock),
        patch.object(dc, "_validate_provider_select", new=AsyncMock(return_value=True)),
        patch.object(dc, "launch_window", new_callable=AsyncMock) as mock_launch,
    ):
        await dc._handle_defaults_yes(query, 100, MagicMock(), ctx)
    request = mock_launch.await_args.args[2]
    assert request.provider_name == "codex"
    assert request.model is None


@pytest.mark.asyncio
async def test_private_button_launches_grok_private_directly(tmp_path) -> None:
    # Tapping 🕵 Private Grok launches immediately: grok, private folder, YOLO.
    user_data = {PENDING_THREAD_ID: 42, PENDING_PROVIDER: "claude"}
    ctx = _context(user_data)
    ctx.bot = MagicMock()
    query = _query()
    priv_cwd = tmp_path / "grok-x"
    priv_cwd.mkdir()
    with (
        patch.object(dc, "_validate_provider_select", new=AsyncMock(return_value=True)),
        patch.object(dc, "launch_window", new_callable=AsyncMock) as mock_launch,
        patch(
            "ccgram.providers.grok_private.create_private_cwd", return_value=priv_cwd
        ),
    ):
        await dc._handle_defaults_private(query, 100, MagicMock(), ctx)
    mock_launch.assert_awaited_once()
    request = mock_launch.await_args.args[2]
    assert request.provider_name == "grok"
    assert request.private is True
    assert request.mode == "yolo"
    assert request.cwd == str(priv_cwd)


@pytest.mark.asyncio
async def test_private_button_stale_when_flow_reset() -> None:
    user_data = {}  # no PENDING_THREAD_ID
    ctx = _context(user_data)
    query = _query()
    with patch.object(dc, "launch_window", new_callable=AsyncMock) as mock_launch:
        await dc._handle_defaults_private(query, 100, MagicMock(), ctx)
    mock_launch.assert_not_awaited()
    query.answer.assert_awaited()
