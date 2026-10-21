"""Adopt Claude Code's own session title, like claude.ai names a chat.

Claude Code appends ``{"type": "ai-title", "aiTitle": …}`` entries to the
transcript after the first exchange. A background task renames sessions that
still carry a placeholder name (``kara MM-DD HH-MM``, ``веб …``, ``workspace-N``)
— the Telegram topic too. Only sessions active since the daemon started are
considered, so old topics are never mass-renamed, and renames are spaced out
to stay clear of Telegram flood control.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from telegram import Bot

logger = logging.getLogger(__name__)

PLACEHOLDER = re.compile(
    r"^(?:(?:kara|веб) \d{2}-\d{2} \d{2}-\d{2}|workspace(?:-\d+)?)$"
)
SCAN_BYTES = 512 * 1024
INTERVAL_S = 15.0
RENAME_GAP_S = 5.0
_MARKER = b"ai-title"

_started = time.time()
_done: set[int] = set()  # sessions already renamed or found without a title source


def is_placeholder(name: str) -> bool:
    return bool(PLACEHOLDER.match(name.strip()))


def find_ai_title(path: Path) -> str | None:
    """Newest ``aiTitle`` in the transcript's head or tail (they repeat)."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            chunks = []
            fh.seek(max(0, size - SCAN_BYTES))
            chunks.append(fh.read())
            if size > SCAN_BYTES:
                fh.seek(0)
                chunks.insert(0, fh.read(SCAN_BYTES))
    except OSError:
        return None
    title = None
    for chunk in chunks:
        for line in chunk.splitlines():
            if _MARKER not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("type") != "ai-title":
                continue
            value = entry.get("aiTitle")
            if isinstance(value, str) and value.strip():
                title = value.strip()
    return title[:120] if title else None


async def _tick(bot: Bot) -> None:
    # Lazy: service pulls the handler graph; this module stays importable.
    from . import service

    for s in await service.list_sessions():
        tid = s["thread_id"]
        if tid in _done or not is_placeholder(s["name"]):
            continue
        if s["last_activity"] < _started:
            continue  # untouched since start: leave old topics alone
        binding = service.find_binding(tid)
        path, _provider = service._transcript_for(binding)
        title = await asyncio.to_thread(find_ai_title, path) if path else None
        if not title:
            continue
        try:
            await service.rename(bot, tid, title)
            logger.info("webui: auto-titled session %d → %r", tid, title)
        except Exception as exc:  # noqa: BLE001 — retry next tick
            logger.info("webui: auto-title of %d failed: %s", tid, exc)
            continue
        _done.add(tid)
        await asyncio.sleep(RENAME_GAP_S)


async def run(bot: Bot) -> None:
    while True:
        await asyncio.sleep(INTERVAL_S)
        try:
            await _tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never let the loop die
            logger.exception("webui auto-title tick failed")


def start(bot: Any) -> asyncio.Task[None]:
    return asyncio.get_running_loop().create_task(run(bot), name="webui-autotitle")
