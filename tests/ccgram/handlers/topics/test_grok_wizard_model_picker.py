"""The mid-wizard model picker stores the pick and advances to the mode picker."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccgram.handlers.topics import provider_mode_callbacks as pmc
from ccgram.handlers.user_state import PENDING_MODEL_ID, PENDING_MODEL_NAME
from ccgram.handlers.topics.topic_creation_draft import PENDING_THREAD_ID
from ccgram.model_catalog import ModelChoice
from ccgram.providers import _ensure_registered


@pytest.fixture(autouse=True)
def _register_providers():
    _ensure_registered()


def _query() -> AsyncMock:
    q = AsyncMock()
    q.answer = AsyncMock()
    return q


def _context(user_data: dict) -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = user_data
    return ctx


@pytest.mark.asyncio
async def test_pick_stores_model_and_shows_mode_picker(tmp_path) -> None:
    user_data = {PENDING_THREAD_ID: 42}
    ctx = _context(user_data)
    query = _query()

    with (
        patch.object(pmc, "safe_edit", new_callable=AsyncMock),
        patch.object(pmc, "_required_selected_path", return_value=str(tmp_path)),
        patch(
            "ccgram.model_catalog.list_models_for_provider",
            new=AsyncMock(
                return_value=[ModelChoice(id="grok-4.5", display_name="Grok 4.5")]
            ),
        ),
    ):
        await pmc._handle_wizard_model_pick(
            query, 100, "wm:grok:grok-4.5", MagicMock(), ctx
        )

    assert user_data[PENDING_MODEL_ID] == "grok-4.5"
    assert user_data[PENDING_MODEL_NAME] == "Grok 4.5"


@pytest.mark.asyncio
async def test_provider_default_clears_model(tmp_path) -> None:
    user_data = {
        PENDING_THREAD_ID: 42,
        PENDING_MODEL_ID: "stale",
        PENDING_MODEL_NAME: "Stale",
    }
    ctx = _context(user_data)
    query = _query()

    with (
        patch.object(pmc, "safe_edit", new_callable=AsyncMock),
        patch.object(pmc, "_required_selected_path", return_value=str(tmp_path)),
    ):
        await pmc._handle_wizard_model_pick(query, 100, "wm:grok:", MagicMock(), ctx)

    assert PENDING_MODEL_ID not in user_data
    assert PENDING_MODEL_NAME not in user_data


@pytest.mark.asyncio
async def test_invalid_model_id_rejected(tmp_path) -> None:
    user_data = {PENDING_THREAD_ID: 42}
    ctx = _context(user_data)
    query = _query()

    with (
        patch.object(pmc, "safe_edit", new_callable=AsyncMock),
        patch.object(pmc, "_required_selected_path", return_value=str(tmp_path)),
    ):
        await pmc._handle_wizard_model_pick(
            query, 100, "wm:grok:bad id; rm -rf /", MagicMock(), ctx
        )

    assert PENDING_MODEL_ID not in user_data
    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_provider_select_routes_grok_to_model_picker(tmp_path) -> None:
    """A model-picker provider shows the model step instead of the mode picker."""
    user_data = {PENDING_THREAD_ID: 42}
    ctx = _context(user_data)
    query = _query()

    with (
        patch.object(pmc, "safe_edit", new_callable=AsyncMock),
        patch.object(pmc, "_required_selected_path", return_value=str(tmp_path)),
        patch.object(
            pmc, "_validate_provider_select", new=AsyncMock(return_value=True)
        ),
        patch.object(
            pmc, "_show_wizard_model_picker", new_callable=AsyncMock
        ) as show_model,
        patch.object(pmc, "build_mode_picker") as build_mode,
    ):
        await pmc._handle_provider_select(query, 100, "prov:grok", MagicMock(), ctx)

    show_model.assert_awaited_once()
    build_mode.assert_not_called()
