"""Tests for the model catalog (CCGRAM-HOTFIX:model-picker)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ccgram import model_catalog
from ccgram.model_catalog import (
    STATIC_FALLBACK,
    ModelChoice,
    _parse_models,
    default_model_label,
    list_models,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_cache():
    model_catalog._cache = None
    yield
    model_catalog._cache = None


class TestParseModels:
    def test_parses_id_and_display_name(self) -> None:
        payload = {
            "data": [
                {"id": "claude-fable-5", "display_name": "Claude Fable 5"},
                {"id": "claude-opus-4-8", "display_name": "Claude Opus 4.8"},
            ]
        }
        models = _parse_models(payload)
        assert models[0] == ModelChoice(id="claude-fable-5", display_name="Claude Fable 5")
        assert len(models) == 2

    def test_missing_display_name_falls_back_to_id(self) -> None:
        models = _parse_models({"data": [{"id": "claude-x"}]})
        assert models[0].display_name == "claude-x"

    def test_skips_entries_without_id(self) -> None:
        models = _parse_models({"data": [{"display_name": "junk"}]})
        assert models == []

    def test_caps_list_length(self) -> None:
        payload = {"data": [{"id": f"m{i}"} for i in range(30)]}
        assert len(_parse_models(payload)) == model_catalog._MAX_MODELS


class TestListModels:
    async def test_no_credentials_returns_fallback(self) -> None:
        with patch.object(model_catalog, "_resolve_auth_headers", return_value=None):
            models = await list_models()
        assert [(m.id, m.display_name) for m in models] == STATIC_FALLBACK

    async def test_network_error_returns_fallback(self) -> None:
        with (
            patch.object(
                model_catalog, "_resolve_auth_headers", return_value={"x-api-key": "k"}
            ),
            patch.object(
                model_catalog.httpx,
                "AsyncClient",
                side_effect=RuntimeError("boom"),
            ),
        ):
            models = await list_models()
        assert [(m.id, m.display_name) for m in models] == STATIC_FALLBACK

    async def test_successful_fetch_is_cached(self) -> None:
        payload = {"data": [{"id": "claude-test-1", "display_name": "Test One"}]}

        resp = AsyncMock()
        resp.raise_for_status = lambda: None
        resp.json = lambda: payload

        client = AsyncMock()
        client.get.return_value = resp
        client.__aenter__.return_value = client

        with (
            patch.object(
                model_catalog, "_resolve_auth_headers", return_value={"x-api-key": "k"}
            ),
            patch.object(
                model_catalog.httpx, "AsyncClient", return_value=client
            ) as client_cls,
        ):
            first = await list_models()
            second = await list_models()

        assert first == [ModelChoice(id="claude-test-1", display_name="Test One")]
        assert second == first
        assert client_cls.call_count == 1  # second call served from cache

    async def test_oauth_headers_include_beta(self, tmp_path) -> None:
        creds = tmp_path / "credentials.json"
        creds.write_text('{"claudeAiOauth": {"accessToken": "tok-123"}}')
        with (
            patch.object(model_catalog, "_CREDENTIALS_PATH", str(creds)),
            patch.dict(model_catalog.os.environ, {"ANTHROPIC_API_KEY": ""}),
        ):
            headers = model_catalog._resolve_auth_headers()
        assert headers == {
            "authorization": "Bearer tok-123",
            "anthropic-beta": "oauth-2025-04-20",
        }


class TestDefaultModelLabel:
    def test_reads_model_from_settings(self, tmp_path) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text('{"model": "opus[1m]"}')
        with patch.object(model_catalog, "_SETTINGS_PATH", str(settings)):
            assert default_model_label() == "opus[1m]"

    def test_missing_file_returns_fallback(self, tmp_path) -> None:
        with patch.object(
            model_catalog, "_SETTINGS_PATH", str(tmp_path / "nope.json")
        ):
            assert default_model_label() == model_catalog._DEFAULT_MODEL_FALLBACK

    def test_no_model_field_returns_fallback(self, tmp_path) -> None:
        settings = tmp_path / "settings.json"
        settings.write_text('{"theme": "dark"}')
        with patch.object(model_catalog, "_SETTINGS_PATH", str(settings)):
            assert default_model_label() == model_catalog._DEFAULT_MODEL_FALLBACK
