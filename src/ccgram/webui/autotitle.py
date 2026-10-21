"""Adopt Claude Code's own session title, like claude.ai names a chat.

Claude Code sometimes appends ``{"type": "ai-title", "aiTitle": …}`` entries
to the transcript — but not for every session. A background task renames
sessions that still carry a placeholder name (``kara MM-DD HH-MM``, ``веб …``,
``workspace-N``) — the Telegram topic too — using that title, or, when Claude
Code wrote none, a short title Haiku makes from the first exchange
(``CCGRAM_WEBUI_TITLE_CMD``, a headless ``claude -p`` wrapper). Only sessions
active in the last few hours are considered, so old topics are never
mass-renamed, and renames are spaced out to stay clear of flood control.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
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

RECENT_S = 6 * 3600
TITLE_MIN, TITLE_MAX, TITLE_RAW_MAX = 2, 60, 80
_DANGLING = {
    "и",
    "в",
    "на",
    "по",
    "для",
    "с",
    "о",
    "от",
    "к",
    "у",
    "а",
    "или",
    "and",
    "of",
    "the",
    "to",
}
GEN_TIMEOUT_S = 90
RETRY_S = 600
_done: set[int] = set()  # sessions already renamed
_failed: dict[int, float] = {}  # session → time of the last failed generation

_TITLE_PROMPT = (
    "Придумай название для чата, как это делает claude.ai: 2–5 слов, не длиннее "
    "40 символов, по-русски "
    "(английские термины можно оставить), без кавычек, точки и эмодзи.\n"
    "Название — о сути ЗАПРОСА пользователя. Ответ ассистента дан только как "
    "контекст: не бери из него темы, о которых пользователь не просил. Если "
    "пользователь лишь проверяет связь или здоровается, так и назови "
    "(например «Проверка связи»).\nОтветь ТОЛЬКО названием.\n\n"
    "Сообщения пользователя:\n{user}\n\nНачало ответа ассистента (может не быть):\n{reply}"
)


def _topic_gone(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "topic_id_invalid" in msg or "thread not found" in msg


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


def _clean_title(raw: str) -> str | None:
    line = next((x.strip() for x in raw.splitlines() if x.strip()), "")
    line = line.strip(" \"'«»“”`*#.").strip()
    line = re.sub(r"^(Название|Title)\s*:\s*", "", line, flags=re.IGNORECASE)
    if not TITLE_MIN <= len(line) <= TITLE_RAW_MAX:
        return None
    if len(line) > TITLE_MAX:  # cut on a word boundary
        line = line[:TITLE_MAX].rsplit(" ", 1)[0].rstrip(" ,;:—-")
        words = line.split()
        while len(words) > 1 and words[-1].lower() in _DANGLING:
            words.pop()
        line = " ".join(words).rstrip(" ,;:—-")
    return line


async def generate_title(path: Path) -> str | None:
    """Ask Haiku (through the headless wrapper) to name the chat."""
    cmd = os.environ.get("CCGRAM_WEBUI_TITLE_CMD", "").strip()
    if not cmd:
        return None
    # Lazy: transcript parser only needed when generating.
    from .transcript import first_exchange

    users, reply = await asyncio.to_thread(first_exchange, path)
    if not users:
        return None
    user = "\n---\n".join(u[:700] for u in users)
    prompt = _TITLE_PROMPT.format(user=user, reply=reply[:500] or "(ответа ещё нет)")
    env = {
        **os.environ,
        "CLAUDE_CALL_MODEL": "haiku",
        "CLAUDE_CALL_PERSONA": "bare",  # anything but "kara" = no persona
        "CLAUDE_CALL_TOOLS": "",
        "CLAUDE_CALL_BUDGET": "0.03",
        "CLAUDE_CALL_TIMEOUT": str(GEN_TIMEOUT_S - 10),
        "KARA_HEADLESS": "1",
    }
    proc = await asyncio.create_subprocess_exec(
        cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
        cwd="/tmp",
    )
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(prompt.encode()), GEN_TIMEOUT_S
        )
    except TimeoutError:
        proc.kill()
        return None
    if proc.returncode != 0:
        return None
    return _clean_title(out.decode(errors="replace"))


def _due(s: dict[str, Any]) -> bool:
    tid = s["thread_id"]
    return (
        tid not in _done
        and is_placeholder(s["name"])
        and s["last_activity"] >= time.time() - RECENT_S  # leave old topics alone
        and time.time() - _failed.get(tid, 0) >= RETRY_S
    )


async def _title_for(tid: int, path: Path) -> str | None:
    """Claude Code's own title, else one Haiku makes from the first message.

    Like claude.ai, the chat is named without waiting for the reply; a session
    with no message yet is simply not ready (not a failure).
    """
    title = await asyncio.to_thread(find_ai_title, path)
    if title:
        return title
    # Lazy: transcript parser only needed here.
    from .transcript import first_exchange

    users, _reply = await asyncio.to_thread(first_exchange, path)
    if not users:
        return None
    title = await generate_title(path)
    if not title:
        _failed[tid] = time.time()
    return title


async def _rename(bot: Bot, tid: int, binding: Any, title: str) -> bool:
    # Lazy: service pulls the handler graph; this module stays importable.
    from . import service

    try:
        await service.rename(bot, tid, title)
    except Exception as exc:  # noqa: BLE001 — retry later
        logger.info("webui: auto-title of %d failed: %s", tid, exc)
        if _topic_gone(exc) and not binding.web_only:
            # The rename is the first thing to notice the topic was deleted
            # in Telegram: let the topic probe confirm it now.
            # Lazy: topics handler graph only needed on this path.
            from ..handlers.topics.topic_lifecycle import note_thread_gone

            note_thread_gone(binding.chat_id, binding.thread_id)
        _failed[tid] = time.time()
        return False
    logger.info("webui: auto-titled session %d → %r", tid, title)
    return True


async def _tick(bot: Bot) -> None:
    # Lazy: service pulls the handler graph; this module stays importable.
    from . import service

    for s in await service.list_sessions():
        if not _due(s):
            continue
        tid = s["thread_id"]
        binding = service.find_binding(tid)
        path, _provider = service._transcript_for(binding)
        title = await _title_for(tid, path) if path else None
        if title and await _rename(bot, tid, binding, title):
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
