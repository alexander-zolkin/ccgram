"""Tests for Grok model discovery (``grok models`` parse, cache, dispatch)."""

from __future__ import annotations

import json

import pytest

from ccgram.providers import grok_models
from ccgram.providers.grok_models import (
    _STATIC_FALLBACK,
    default_grok_model_label,
    list_grok_models,
    parse_grok_models_output,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    grok_models._reset_cache()
    yield
    grok_models._reset_cache()


# ── parse_grok_models_output ──────────────────────────────────────────────


def test_parse_typical_output() -> None:
    text = (
        "You are logged in with grok.com.\n"
        "\n"
        "Default model: grok-4.5\n"
        "\n"
        "Available models:\n"
        "  * grok-4.5 (default)\n"
        "  * grok-code-fast-1\n"
    )
    default_id, models = parse_grok_models_output(text)
    assert default_id == "grok-4.5"
    assert ("grok-4.5", "Grok 4.5") in models
    assert any(mid == "grok-code-fast-1" for mid, _ in models)


def test_parse_infers_default_from_marker() -> None:
    text = "Available models:\n  * grok-4.5 (default)\n  * grok-4\n"
    default_id, models = parse_grok_models_output(text)
    assert default_id == "grok-4.5"
    assert [mid for mid, _ in models] == ["grok-4.5", "grok-4"]


def test_parse_with_description_suffix() -> None:
    text = "Available models:\n  * grok-4 — Reasoning model\n"
    _default, models = parse_grok_models_output(text)
    assert models == [("grok-4", "Reasoning model")]


def test_parse_empty_output() -> None:
    default_id, models = parse_grok_models_output("")
    assert default_id == ""
    assert models == []


def test_parse_ignores_lines_before_available_header() -> None:
    text = (
        "Default model: grok-4.5\nSome banner line\nAvailable models:\n  * grok-4.5\n"
    )
    _default, models = parse_grok_models_output(text)
    assert [mid for mid, _ in models] == ["grok-4.5"]


# ── cache-file fallback + dispatch ────────────────────────────────────────


def test_list_grok_models_reads_cache_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    monkeypatch.delenv("CCGRAM_GROK_MODEL", raising=False)
    # Force the subprocess source to fail so the cache file is used.
    monkeypatch.setattr(grok_models, "_from_subprocess", lambda: ("", []))
    (tmp_path / "models_cache.json").write_text(
        json.dumps(
            {
                "models": {
                    "grok-4.5": {
                        "info": {"id": "grok-4.5", "name": "Grok 4.5", "hidden": False}
                    },
                    "grok-hidden": {
                        "info": {"id": "grok-hidden", "name": "Hidden", "hidden": True}
                    },
                }
            }
        )
    )
    models = list_grok_models()
    ids = [mid for mid, _ in models]
    assert "grok-4.5" in ids
    assert "grok-hidden" not in ids  # hidden models are filtered out


def test_list_grok_models_static_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_HOME", str(tmp_path))  # no cache file present
    monkeypatch.setattr(grok_models, "_from_subprocess", lambda: ("", []))
    assert list_grok_models() == _STATIC_FALLBACK


def test_default_label_prefers_env(monkeypatch) -> None:
    monkeypatch.setenv("CCGRAM_GROK_MODEL", "grok-code-fast-1")
    assert default_grok_model_label() == "grok-code-fast-1"


def test_default_label_falls_back(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CCGRAM_GROK_MODEL", raising=False)
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    monkeypatch.setattr(grok_models, "_from_subprocess", lambda: ("", []))
    assert default_grok_model_label() == "grok-4.5"


def test_default_label_never_shells_out(tmp_path, monkeypatch) -> None:
    """The label runs on the event loop, so it must NOT spawn ``grok models``."""
    monkeypatch.delenv("CCGRAM_GROK_MODEL", raising=False)
    monkeypatch.setenv("GROK_HOME", str(tmp_path))

    def _boom():
        raise AssertionError("default_grok_model_label must not shell out")

    monkeypatch.setattr(grok_models, "_from_subprocess", _boom)
    assert default_grok_model_label() == "grok-4.5"


def test_default_label_reads_settings_json(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CCGRAM_GROK_MODEL", raising=False)
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    (tmp_path / "settings.json").write_text('{"model": "grok-code-fast-1"}')
    assert default_grok_model_label() == "grok-code-fast-1"


# ── model_catalog dispatch ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_catalog_dispatch_grok(monkeypatch) -> None:
    from ccgram.model_catalog import (
        default_model_label_for_provider,
        list_models_for_provider,
    )

    monkeypatch.setattr(
        grok_models, "list_grok_models", lambda: [("grok-4.5", "Grok 4.5")]
    )
    monkeypatch.setattr(grok_models, "default_grok_model_label", lambda: "grok-4.5")
    models = await list_models_for_provider("grok")
    assert [(m.id, m.display_name) for m in models] == [("grok-4.5", "Grok 4.5")]
    assert default_model_label_for_provider("grok") == "grok-4.5"
