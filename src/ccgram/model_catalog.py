"""Anthropic model catalog for the session-start model picker.

CCGRAM-HOTFIX:model-picker — fetches the live model list from the Anthropic
API (``GET /v1/models``) so the quick-start prompt can offer a model choice.

Auth resolution (first match wins):
  1. ``ANTHROPIC_API_KEY`` env var → ``x-api-key`` header.
  2. Claude Code OAuth token from ``~/.claude/.credentials.json``
     (``claudeAiOauth.accessToken``) → ``Authorization: Bearer`` +
     ``anthropic-beta: oauth-2025-04-20`` (required for OAuth on the API).

Failures never propagate: on any error (no creds, network, bad JSON) the
static fallback list is returned, so the picker always renders.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import structlog

logger = structlog.get_logger()

_MODELS_URL = "https://api.anthropic.com/v1/models"
_ANTHROPIC_VERSION = "2023-06-01"
_OAUTH_BETA = "oauth-2025-04-20"
_CREDENTIALS_PATH = "~/.claude/.credentials.json"
_SETTINGS_PATH = "~/.claude/settings.json"
_DEFAULT_MODEL_FALLBACK = "Claude CLI default"
_TIMEOUT_S = 8.0
_CACHE_TTL_S = 3600.0
_FAILURE_TTL_S = 60.0  # don't hammer the API after a failure
_MAX_MODELS = 8  # keep the inline keyboard sane

# Used when the API is unreachable or no credentials are available.
STATIC_FALLBACK: list[tuple[str, str]] = [
    ("claude-fable-5", "Claude Fable 5"),
    ("claude-opus-4-8", "Claude Opus 4.8"),
    ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
    ("claude-haiku-4-5", "Claude Haiku 4.5"),
]


@dataclass(frozen=True)
class ModelChoice:
    """One selectable model: API id + human-readable name."""

    id: str
    display_name: str


_cache: tuple[float, float, list[ModelChoice]] | None = None  # (ts, ttl, models)


def _fallback_models() -> list[ModelChoice]:
    return [ModelChoice(id=i, display_name=n) for i, n in STATIC_FALLBACK]


def default_model_label() -> str:
    """Best-effort label for the model a fresh ``claude`` launch will use.

    CCGRAM-HOTFIX:model-picker — reads the ``model`` field from
    ``~/.claude/settings.json`` (the model the claude CLI uses when no
    ``--model`` flag is passed, e.g. ``"opus[1m]"``). Returns a generic
    fallback string when the file is unreadable or has no model set.
    Never raises.
    """
    try:
        settings = json.loads(Path(_SETTINGS_PATH).expanduser().read_text())
        model = settings.get("model")
    except OSError, ValueError:
        return _DEFAULT_MODEL_FALLBACK
    if isinstance(model, str) and model.strip():
        return model.strip()
    return _DEFAULT_MODEL_FALLBACK


def _resolve_auth_headers() -> dict[str, str] | None:
    """Build auth headers, or None when no credential source is available."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        return {"x-api-key": api_key}
    try:
        creds = json.loads(Path(_CREDENTIALS_PATH).expanduser().read_text())
        token = creds.get("claudeAiOauth", {}).get("accessToken", "")
    except OSError, ValueError:
        return None
    if not token:
        return None
    return {
        "authorization": f"Bearer {token}",
        "anthropic-beta": _OAUTH_BETA,
    }


def _parse_models(payload: dict) -> list[ModelChoice]:
    """Extract (id, display_name) pairs from a /v1/models response page."""
    models: list[ModelChoice] = []
    for entry in payload.get("data", []):
        model_id = entry.get("id")
        if not model_id:
            continue
        models.append(
            ModelChoice(id=model_id, display_name=entry.get("display_name") or model_id)
        )
    # The API returns newest-first (created_at desc); trust that order and cap
    # the list so the Telegram keyboard stays one screen tall.
    return models[:_MAX_MODELS]


async def list_models() -> list[ModelChoice]:
    """Return selectable models, live from the API when possible.

    Never raises. Results are cached for an hour; failures are cached for a
    minute and served from the static fallback.
    """
    global _cache
    now = time.monotonic()
    if _cache is not None:
        ts, ttl, cached = _cache
        if now - ts < ttl:
            return cached

    headers = _resolve_auth_headers()
    if headers is None:
        logger.warning("model_catalog: no Anthropic credentials found, using fallback")
        _cache = (now, _FAILURE_TTL_S, _fallback_models())
        return _cache[2]

    headers["anthropic-version"] = _ANTHROPIC_VERSION
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(_MODELS_URL, headers=headers, params={"limit": 50})
            resp.raise_for_status()
            models = _parse_models(resp.json())
    except Exception as e:  # noqa: BLE001 — the picker must always render
        logger.warning("model_catalog: /v1/models fetch failed (%s), using fallback", e)
        _cache = (now, _FAILURE_TTL_S, _fallback_models())
        return _cache[2]

    if not models:
        _cache = (now, _FAILURE_TTL_S, _fallback_models())
        return _cache[2]

    _cache = (now, _CACHE_TTL_S, models)
    logger.info("model_catalog: fetched %d models from API", len(models))
    return models


# ── Provider-aware dispatch ───────────────────────────────────────────────
#
# The session-creation model picker is provider-agnostic: it asks the catalog
# for a provider's models and the label of its default. Claude keeps the live
# Anthropic ``/v1/models`` path above; Grok discovers models via its own CLI
# (``ccgram.providers.grok_models``), kept out of this module so the Grok path
# never drags in httpx / Anthropic auth.


async def list_models_for_provider(provider: str) -> list[ModelChoice]:
    """Return selectable models for *provider*. Never raises.

    Falls back to the Claude/Anthropic catalog for unknown providers so callers
    (which already gate on ``supports_model_picker``) always get a usable list.
    """
    if provider == "grok":
        # Lazy: asyncio.to_thread only needed to offload the blocking grok CLI.
        import asyncio

        # Lazy: keep the Grok CLI shell-out off the Anthropic import path.
        from ccgram.providers.grok_models import list_grok_models

        pairs = await asyncio.to_thread(list_grok_models)
        return [ModelChoice(id=i, display_name=n) for i, n in pairs]
    return await list_models()


def default_model_label_for_provider(provider: str) -> str:
    """Best-effort label for the model a fresh launch of *provider* will use."""
    if provider == "grok":
        # Lazy: keep the Grok CLI shell-out off the Anthropic import path.
        from ccgram.providers.grok_models import default_grok_model_label

        return default_grok_model_label()
    return default_model_label()
