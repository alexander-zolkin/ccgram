"""Quick-start model picker: tapping a model returns to the prompt by itself.

CCGRAM-HOTFIX:model-pick-no-refetch — the pick handler used to re-list the
catalog (claude: an 8s-timeout HTTPS call; grok: a CLI subprocess) *before*
``query.answer``. Blowing Telegram's ~10s callback deadline made the answer
raise, which aborted the handler before the edit — the picker stayed on screen
and the user had to tap ⬅️ Back to get back to the prompt.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccgram.handlers.callback_data import CB_MODEL_BACK, CB_MODEL_PICK
from ccgram.handlers.topics import directory_callbacks as dc
from ccgram.handlers.topics.directory_browser import build_model_picker
from ccgram.handlers.user_state import (
    PENDING_MODEL_CHOICES,
    PENDING_MODEL_ID,
    PENDING_MODEL_NAME,
    PENDING_THREAD_ID,
)
from ccgram.model_catalog import ModelChoice
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


def test_picker_offers_back_and_marks_selection() -> None:
    _text, kb = build_model_picker(
        [("claude-opus-5", "Opus 5"), ("claude-sonnet-5", "Sonnet 5")],
        selected_id="claude-opus-5",
        provider_label="Claude",
    )
    rows = [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]
    opus = next(t for t, cb in rows if cb == f"{CB_MODEL_PICK}claude-opus-5")
    assert opus.startswith("✅")
    assert any(cb == CB_MODEL_BACK for _t, cb in rows)


@pytest.mark.asyncio
async def test_opening_the_picker_records_offered_choices() -> None:
    user_data = {PENDING_THREAD_ID: 42}
    ctx = _context(user_data)
    query = _query()
    update = MagicMock()

    with (
        patch.object(dc, "safe_edit", new_callable=AsyncMock),
        patch.object(dc, "get_thread_id", return_value=42),
        patch(
            "ccgram.model_catalog.list_models_for_provider",
            new=AsyncMock(
                return_value=[ModelChoice(id="claude-opus-5", display_name="Opus 5")]
            ),
        ),
    ):
        await dc._handle_defaults_model(query, update, ctx)

    assert user_data[PENDING_MODEL_CHOICES] == {"claude-opus-5": "Opus 5"}


@pytest.mark.asyncio
async def test_pick_returns_to_the_prompt_without_touching_the_catalog() -> None:
    user_data = {
        PENDING_THREAD_ID: 42,
        PENDING_MODEL_CHOICES: {"claude-opus-5": "Opus 5"},
    }
    ctx = _context(user_data)
    query = _query()

    with (
        patch.object(dc, "safe_edit", new_callable=AsyncMock) as mock_edit,
        patch(
            "ccgram.model_catalog.list_models_for_provider", new_callable=AsyncMock
        ) as mock_catalog,
    ):
        await dc._handle_model_pick(
            query, 100, f"{CB_MODEL_PICK}claude-opus-5", MagicMock(), ctx
        )

    mock_catalog.assert_not_awaited()
    assert user_data[PENDING_MODEL_ID] == "claude-opus-5"
    assert user_data[PENDING_MODEL_NAME] == "Opus 5"
    mock_edit.assert_awaited_once()
    assert "Use default settings?" in mock_edit.await_args.args[1]
    assert "Opus 5" in mock_edit.await_args.args[1]


@pytest.mark.asyncio
async def test_pick_answers_before_editing() -> None:
    """The answer must go out first — a late answer is what used to abort the edit."""
    order: list[str] = []
    user_data = {
        PENDING_THREAD_ID: 42,
        PENDING_MODEL_CHOICES: {"claude-opus-5": "Opus 5"},
    }
    ctx = _context(user_data)
    query = _query()
    query.answer.side_effect = lambda *a, **kw: order.append("answer")

    async def _edit(*_a, **_kw):
        order.append("edit")

    with patch.object(dc, "safe_edit", new=AsyncMock(side_effect=_edit)):
        await dc._handle_model_pick(
            query, 100, f"{CB_MODEL_PICK}claude-opus-5", MagicMock(), ctx
        )

    assert order == ["answer", "edit"]


@pytest.mark.asyncio
async def test_pick_with_no_recorded_choices_still_returns() -> None:
    """Stale keyboard after a daemon restart: name degrades to the id, flow lives."""
    user_data = {PENDING_THREAD_ID: 42}
    ctx = _context(user_data)
    query = _query()

    with patch.object(dc, "safe_edit", new_callable=AsyncMock) as mock_edit:
        await dc._handle_model_pick(
            query, 100, f"{CB_MODEL_PICK}claude-opus-5", MagicMock(), ctx
        )

    assert user_data[PENDING_MODEL_NAME] == "claude-opus-5"
    mock_edit.assert_awaited_once()
    assert "Use default settings?" in mock_edit.await_args.args[1]
