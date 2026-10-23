"""Rewind a Claude session to just before one of its prompts («откатить сюда»).

Claude Code's own ``/rewind`` keeps the cut only in memory until the next
prompt, so it is not used. Instead (verified against Claude Code 2.1.309):

1. Claude stops in its pane (Escape, Ctrl-C twice; SIGTERM as a last resort).
2. Files, when asked: ``claude -p --resume <sid> --rewind-files <uuid>`` with
   ``CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING=1`` puts tracked files back to
   how they were when that prompt was sent.
3. The transcript gets ``{"type":"last-prompt","leafUuid":<prompt's parent>,
   "explicit":true,"rewound":true}`` — the entry Claude Code itself writes for
   SDK rewinds; a resume then loads the conversation only up to that point.
4. Claude starts again in the same pane with the same flags plus
   ``--resume <sid>``. A sleeping session is only marked; it wakes rewound.

The abandoned lines stay in the file (Claude Code never truncates it). The
page hides them: each rewind records the byte range from the prompt's line to
the marker in ``webui_rewinds.json``, and transcript reads skip it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Any

from ..config import config
from ..multiplexer import multiplexer as tmux_manager

logger = logging.getLogger(__name__)

_STORE = "webui_rewinds.json"
# Flags of the running claude that are not carried over to the relaunch, and
# which of them take a value.
_DROP = {"--resume", "-r", "--continue", "-c", "--session-id", "--fork-session"}
_VALUED = {
    "--resume", "-r", "--session-id", "--model", "--effort", "--permission-mode",
    "--add-dir", "--settings", "--mcp-config", "--append-system-prompt",
    "--system-prompt", "--agent", "--name", "-n", "--fallback-model",
    "--allowedTools", "--allowed-tools", "--disallowedTools",
    "--disallowed-tools", "--plugin-dir", "--setting-sources", "--betas",
}  # fmt: skip
_cache: dict[str, Any] = {"mtime": None, "data": {}}
_UUID = re.compile(r"[0-9a-fA-F-]{8,64}")


class RewindError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


# ── hidden ranges ────────────────────────────────────────────────────────────


def _store_path() -> Path:
    return config.config_dir / _STORE


def _load() -> dict[str, list[list[int]]]:
    path = _store_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _cache["mtime"] != mtime:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            data = {}
        _cache.update(mtime=mtime, data=data if isinstance(data, dict) else {})
    return _cache["data"]


def hidden_ranges(transcript_path: Path) -> list[tuple[int, int]]:
    """Byte ranges of ``transcript_path`` that rewinds abandoned."""
    return [(a, b) for a, b in _load().get(str(transcript_path), [])]


def _remember(transcript_path: Path, start: int, end: int) -> None:
    data = dict(_load())
    ranges = [*data.get(str(transcript_path), []), [start, end]]
    data[str(transcript_path)] = ranges
    path = _store_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


# ── the prompt ───────────────────────────────────────────────────────────────


def find_prompt(transcript_path: Path, uuid: str) -> tuple[int, dict[str, Any]] | None:
    """(line offset, entry) of the user prompt ``uuid``, outside hidden lines."""
    needle = f'"{uuid}"'.encode()  # cheap prefilter; the parse decides
    hidden = hidden_ranges(transcript_path)
    found = None
    offset = 0
    with transcript_path.open("rb") as fh:
        for raw in fh:
            at, offset = offset, offset + len(raw)
            if needle not in raw or any(a <= at < b for a, b in hidden):
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("uuid") == uuid:
                found = (at, entry)
    return found


def prompt_text(entry: dict[str, Any]) -> str:
    # Lazy: transcript is a sibling leaf; keeps this module importable alone.
    from . import transcript

    items = transcript.parse_entry(entry)
    return next((it["text"] for it in items if it["kind"] == "user"), "")


def _check_prompt(entry: dict[str, Any]) -> None:
    if entry.get("type") != "user" or entry.get("isSidechain") or entry.get("isMeta"):
        raise RewindError("это не сообщение человека")
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    ):
        raise RewindError("это не сообщение человека")


# ── the process ──────────────────────────────────────────────────────────────


def _is_claude(argv: list[str]) -> bool:
    return bool(argv) and Path(argv[0]).name == "claude"


def relaunch_argv(argv: list[str], session_id: str) -> list[str]:
    """The running claude's command line, resuming ``session_id`` instead."""
    out = [argv[0]]
    i = 1
    while i < len(argv):
        arg = argv[i]
        name = arg.split("=", 1)[0]
        takes = name in _VALUED and "=" not in arg
        if name in _DROP:
            i += 2 if takes else 1
            continue
        if not arg.startswith("-"):
            i += 1  # a prompt given at launch: never replay it
            continue
        out.append(arg)
        if takes and i + 1 < len(argv):
            out.append(argv[i + 1])
            i += 1
        i += 1
    return [*out, "--resume", session_id]


async def _foreground_claude(window_id: str) -> list[str] | None:
    fg = await tmux_manager.foreground(window_id)
    return fg.argv if fg and _is_claude(fg.argv) else None


async def _wait_gone(window_id: str, seconds: float) -> bool:
    for _ in range(int(seconds / 0.25)):
        if await _foreground_claude(window_id) is None:
            return True
        await asyncio.sleep(0.25)
    return await _foreground_claude(window_id) is None


async def stop_claude(window_id: str) -> None:
    await tmux_manager.send_keys(window_id, "Escape", enter=False, literal=False)
    await asyncio.sleep(0.5)
    for _ in range(2):
        await tmux_manager.send_keys(window_id, "C-c", enter=False, literal=False)
        await asyncio.sleep(0.35)
    if await _wait_gone(window_id, 4):
        return
    fg = await tmux_manager.foreground(window_id)
    if fg and _is_claude(fg.argv):
        with contextlib.suppress(OSError):
            os.kill(fg.pid, 15)
    if not await _wait_gone(window_id, 6):
        raise RewindError("Claude не остановился — попробуй ещё раз", 502)


async def rewind_files(claude: str, session_id: str, uuid: str, cwd: str) -> str:
    env = {**os.environ, "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING": "1"}
    proc = await asyncio.create_subprocess_exec(
        claude,
        "-p",
        "--resume",
        session_id,
        "--rewind-files",
        uuid,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 90)
    except TimeoutError:
        proc.kill()
        raise RewindError("откат файлов не уложился в 90 с", 504) from None
    text = out.decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        raise RewindError(f"файлы не откатились: {text[-300:]}", 502)
    return text


def mark(transcript_path: Path, session_id: str, leaf: str | None) -> int:
    """Append the rewind entry; return the byte offset it was written at."""
    line = json.dumps(
        {
            "type": "last-prompt",
            "leafUuid": leaf,
            "explicit": True,
            "rewound": True,
            "sessionId": session_id,
        }
    )
    with transcript_path.open("ab") as fh:
        at = fh.tell()
        fh.write(line.encode() + b"\n")
    return at


async def rewind(
    *,
    window_id: str | None,
    transcript_path: Path,
    cwd: str,
    uuid: str,
    files: bool,
) -> dict[str, Any]:
    """Rewind the session to just before prompt ``uuid``; see module doc."""
    if not _UUID.fullmatch(uuid):
        raise RewindError("bad message id", 400)
    found = await asyncio.to_thread(find_prompt, transcript_path, uuid)
    if found is None:
        raise RewindError("сообщение не найдено в транскрипте", 404)
    offset, entry = found
    _check_prompt(entry)
    session_id = transcript_path.stem
    argv = await _foreground_claude(window_id) if window_id else None
    if argv is not None:
        await stop_claude(window_id)  # type: ignore[arg-type]
    claude = (argv and shutil.which(argv[0])) or shutil.which("claude") or "claude"
    restored = ""
    if files:
        restored = await rewind_files(claude, session_id, uuid, cwd)
    at = await asyncio.to_thread(
        mark, transcript_path, session_id, entry.get("parentUuid")
    )
    await asyncio.to_thread(_remember, transcript_path, offset, at)
    if argv is not None:
        # The pane's shell already exports the thresholds that keep Claude
        # Code's resume-size menu away (multiplexer.tmux.create_window).
        cmd = shlex.join(relaunch_argv(argv, session_id))
        await tmux_manager.send_keys(window_id, cmd, enter=True, literal=True)  # type: ignore[arg-type]
        for _ in range(40):
            if await _foreground_claude(window_id):  # type: ignore[arg-type]
                break
            await asyncio.sleep(0.25)
    logger.info(
        "webui rewind: %s to before %s (files=%s, relaunched=%s)",
        session_id,
        uuid,
        files,
        argv is not None,
    )
    return {
        "ok": True,
        "text": prompt_text(entry),
        "files": restored,
        "relaunched": argv is not None,
    }
