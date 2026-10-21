"""Small web-UI preferences shared by every browser (pinned sessions).

Persisted to ``<CCGRAM_DIR>/webui_prefs.json``; stdlib-only leaf module.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

_prefs: dict[str, Any] | None = None


def _path() -> Path:
    base = os.getenv("CCGRAM_DIR") or os.path.expanduser("~/.ccgram")
    return Path(base) / "webui_prefs.json"


def _load() -> dict[str, Any]:
    global _prefs
    if _prefs is None:
        try:
            _prefs = json.loads(_path().read_text(encoding="utf-8"))
        except OSError, ValueError:
            _prefs = {}
        if not isinstance(_prefs, dict):
            _prefs = {}
    return _prefs


def _save() -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".webui_prefs.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(_load(), fh, ensure_ascii=False)
    os.replace(tmp, path)


def reset_for_testing() -> None:
    global _prefs
    _prefs = None


def pinned() -> set[int]:
    return {int(x) for x in _load().get("pinned", []) if isinstance(x, int)}


def set_pinned(session_id: int, on: bool) -> None:
    pins = pinned()
    if on:
        pins.add(session_id)
    else:
        pins.discard(session_id)
    _load()["pinned"] = sorted(pins)
    _save()
