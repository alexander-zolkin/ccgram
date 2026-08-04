"""Grok Build model discovery for the session-start model picker.

Resolution order (first non-empty wins), all failing open to the next source:

  1. ``grok models`` subprocess — the authoritative live list for the logged-in
     account, including which model is the default.
  2. ``$GROK_HOME/models_cache.json`` — Grok's own on-disk cache, used when the
     CLI can't be run (not installed, not logged in, no leader socket).
  3. A small static fallback so the picker always renders.

Everything here is plain-stdlib and dependency-free (no httpx / Anthropic
coupling) so ``ccgram.model_catalog`` can dispatch to it lazily without
dragging network libraries onto the Grok path.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time

import structlog

from ccgram.providers.grok_discovery import grok_home

logger = structlog.get_logger()

# id → display name.  ``grok-4.5`` is the shipping default as of 2026-07-17.
_STATIC_FALLBACK: list[tuple[str, str]] = [
    ("grok-4.5", "Grok 4.5"),
    ("grok-code-fast-1", "Grok Code Fast 1"),
]

_DEFAULT_FALLBACK_ID = "grok-4.5"

_SUBPROCESS_TIMEOUT_S = 4.0
_CACHE_TTL_S = 900.0
_FAILURE_TTL_S = 60.0
_MAX_MODELS = 8

# (monotonic_ts, ttl, default_id, models)
_cache: tuple[float, float, str, list[tuple[str, str]]] | None = None


def _grok_command() -> str:
    """First token of ``CCGRAM_GROK_COMMAND`` (or ``grok``) — the base binary."""
    override = os.environ.get("CCGRAM_GROK_COMMAND", "").strip()
    if override:
        try:
            parts = shlex.split(override)
        except ValueError:
            parts = override.split()
        if parts:
            return parts[0]
    return "grok"


def parse_grok_models_output(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Parse ``grok models`` stdout into ``(default_id, [(id, name), …])``.

    Expected shape::

        You are logged in with grok.com.

        Default model: grok-4.5

        Available models:
          * grok-4.5 (default)
          * grok-code-fast-1

    Robust to leading bullets (``*``/``-``), an inline ``(default)`` marker, and
    an optional ``id — name`` / ``id (name)`` description suffix.
    """
    default_id = ""
    models: list[tuple[str, str]] = []
    seen: set[str] = set()
    in_list = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if lower.startswith("default model:"):
            default_id = stripped.split(":", 1)[1].strip()
            continue
        if lower.startswith("available models"):
            in_list = True
            continue
        if not in_list:
            continue
        # A model row: strip a leading bullet, then take the first token as id.
        entry = stripped.lstrip("*-•").strip()
        if not entry:
            continue
        is_default = "(default)" in entry.lower()
        entry = entry.replace("(default)", "").replace("(Default)", "").strip()
        # Split an "id — name" / "id (name)" description off the id token.
        token = entry.split()[0]
        model_id = token.strip()
        if not model_id or model_id in seen:
            continue
        # Best-effort display name from the remainder, else title-case the id.
        remainder = entry[len(token) :].strip(" -—()")
        name = remainder or _prettify(model_id)
        seen.add(model_id)
        models.append((model_id, name))
        if is_default and not default_id:
            default_id = model_id
    if default_id and default_id not in seen and models:
        # Default reported but not listed — surface it anyway, at the top.
        models.insert(0, (default_id, _prettify(default_id)))
    return default_id, models[:_MAX_MODELS]


def _prettify(model_id: str) -> str:
    """Turn ``grok-4.5`` into ``Grok 4.5`` for a friendly button label."""
    return model_id.replace("-", " ").title().replace("Grok ", "Grok ")


def _from_subprocess() -> tuple[str, list[tuple[str, str]]]:
    """Run ``grok models`` and parse it; ``("", [])`` on any failure."""
    cmd = _grok_command()
    try:
        result = subprocess.run(
            [cmd, "models"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("grok models subprocess failed: %s", exc)
        return "", []
    if result.returncode != 0:
        logger.debug(
            "grok models exited %d: %s", result.returncode, result.stderr[:200]
        )
        return "", []
    return parse_grok_models_output(result.stdout)


def _from_cache_file() -> tuple[str, list[tuple[str, str]]]:
    """Read ``$GROK_HOME/models_cache.json``; ``("", [])`` on any failure."""
    path = grok_home() / "models_cache.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return "", []
    if not isinstance(data, dict):
        return "", []
    models_obj = data.get("models")
    if not isinstance(models_obj, dict):
        return "", []
    models: list[tuple[str, str]] = []
    for model_id, entry in models_obj.items():
        if not isinstance(model_id, str) or not model_id:
            continue
        name = model_id
        hidden = False
        if isinstance(entry, dict):
            info = entry.get("info")
            if isinstance(info, dict):
                hidden = bool(info.get("hidden"))
                display = info.get("name")
                if isinstance(display, str) and display:
                    name = display
        if hidden:
            continue
        models.append((model_id, name))
    return "", models[:_MAX_MODELS]


def _resolve() -> tuple[str, list[tuple[str, str]]]:
    default_id, models = _from_subprocess()
    if models:
        return default_id or _DEFAULT_FALLBACK_ID, models
    _default, cached = _from_cache_file()
    if cached:
        return _DEFAULT_FALLBACK_ID, cached
    return _DEFAULT_FALLBACK_ID, list(_STATIC_FALLBACK)


def list_grok_models() -> list[tuple[str, str]]:
    """Return selectable ``(id, display_name)`` pairs for Grok. Never raises."""
    return _load()[1]


def default_grok_model_label() -> str:
    """Best-effort label for the model a fresh ``grok`` launch will use.

    Deliberately cheap and **subprocess-free** — this runs on the asyncio event
    loop while rendering the quick-start prompt, so it must never shell out
    (a blocking ``grok models`` here froze all menu navigation). Resolution:
    ``CCGRAM_GROK_MODEL`` → the on-disk grok ``settings.json`` model →
    ``grok-4.5``. The live model list stays in ``list_grok_models`` (the picker),
    which callers run off-thread.
    """
    override = os.environ.get("CCGRAM_GROK_MODEL", "").strip()
    if override:
        return override
    # If a live list was already fetched (picker opened), reuse its cached
    # default; otherwise fall back to a cheap on-disk read — never shell out.
    if _cache is not None:
        default_id = _cache[2]
        if default_id:
            return default_id
    try:
        data = json.loads((grok_home() / "settings.json").read_text(encoding="utf-8"))
        model = data.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
    except (OSError, json.JSONDecodeError):
        pass
    return _DEFAULT_FALLBACK_ID


def _load() -> tuple[str, list[tuple[str, str]]]:
    """Return ``(default_id, models)`` with TTL caching around ``_resolve``."""
    global _cache
    now = time.monotonic()
    if _cache is not None:
        ts, ttl, default_id, models = _cache
        if now - ts < ttl:
            return default_id, models
    default_id, models = _resolve()
    ttl = _CACHE_TTL_S if models and models != _STATIC_FALLBACK else _FAILURE_TTL_S
    _cache = (now, ttl, default_id, models)
    return default_id, models


def _reset_cache() -> None:
    """Clear the module cache (tests only)."""
    global _cache
    _cache = None
