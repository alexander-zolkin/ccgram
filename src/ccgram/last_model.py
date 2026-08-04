"""Remember the last model Alexander explicitly picked, per provider.

CCGRAM-HOTFIX:model-picker — the session-start model picker stores its choice
in ``context.user_data`` (``PENDING_MODEL_ID``/``PENDING_MODEL_NAME``), which is
per-conversation and wiped on every launch and on bot restart. This module
persists the *last explicitly selected* model to a small JSON file so a fresh
quick-start prompt defaults to it instead of the CLI default.

Keyed by provider (``claude``/``grok``) because each provider has its own model
catalog. Only explicit picks are remembered — launching on the provider default
never overwrites the remembered model.

Every function is best-effort: any error (missing dir, unreadable/corrupt JSON,
permission) is swallowed and treated as "nothing remembered", so the picker
flow never breaks because of persistence.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import structlog

logger = structlog.get_logger()

_FILE_NAME = "last_model.json"


def _store_path() -> Path:
    """Resolve ``<CCGRAM_DIR>/last_model.json`` (defaults to ``~/.ccgram``).

    ``CCGRAM_DIR`` is the same config dir that holds ``events.jsonl`` (see
    ``cli.py``), so the remembered model sits next to the rest of ccgram state.
    """
    base = os.environ.get("CCGRAM_DIR", "~/.ccgram")
    return Path(base).expanduser() / _FILE_NAME


def _load() -> dict[str, dict[str, str]]:
    """Return the whole store as ``{provider: {"id": ..., "name": ...}}``.

    Empty dict on any failure; never raises.
    """
    try:
        raw = json.loads(_store_path().read_text())
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def recall_model(provider: str) -> tuple[str, str] | None:
    """Return ``(model_id, display_name)`` last picked for *provider*, else None.

    None when nothing is remembered or the stored entry is malformed.
    """
    entry = _load().get(provider)
    if not isinstance(entry, dict):
        return None
    model_id = entry.get("id")
    name = entry.get("name")
    if isinstance(model_id, str) and model_id.strip():
        return model_id, name if isinstance(name, str) and name.strip() else model_id
    return None


def remember_model(provider: str, model_id: str, display_name: str) -> None:
    """Persist *model_id*/*display_name* as the last pick for *provider*.

    Best-effort atomic write (temp file + ``os.replace``). Never raises — a
    failed write just means the next prompt falls back to the CLI default.
    """
    if not (provider and model_id):
        return
    store = _load()
    store[provider] = {"id": model_id, "name": display_name or model_id}
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # atomic: write to a temp file in the same dir, then rename over the target
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".last_model.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(store, fh)
            os.replace(tmp, path)
        finally:
            # if replace failed, don't leak the temp file
            if os.path.exists(tmp):
                os.unlink(tmp)
    except OSError as e:
        logger.warning("last_model: could not persist pick (%s)", e)
