"""Grok Build status snapshot helpers.

Builds a Telegram-friendly status message from a Grok session directory.  Grok
slash commands like ``/session-info`` render in the TUI and may not append to
``chat_history.jsonl``, so this transcript/metadata snapshot is a reliable
fallback for ``/status``-style queries.

The snapshot draws on the three plain-JSON state files that live alongside the
``chat_history.jsonl`` transcript: ``summary.json`` (id, cwd, model, counts),
``signals.json`` (token / tool counters), plus the transcript tail.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _display_cwd(cwd: str) -> str:
    home = str(Path.home())
    return cwd.replace(home, "~", 1) if cwd.startswith(home) else cwd


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _entry_has_assistant_output(entry: dict[str, Any]) -> bool:
    """True when a ``chat_history.jsonl`` line carries assistant-visible output."""
    if entry.get("type") != "assistant":
        return False
    content = entry.get("content")
    if isinstance(content, str) and content.strip():
        return True
    if isinstance(content, list):
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                and block["text"].strip()
            ):
                return True
    tool_calls = entry.get("tool_calls")
    return isinstance(tool_calls, list) and bool(tool_calls)


def has_grok_assistant_output_since(transcript_path: str, offset: int) -> bool:
    """Check whether the transcript has assistant output after entry *offset*.

    ``offset`` is an entry index (grok is read whole-file, not by byte offset).
    Decoding uses ``errors="replace"`` so a transient partial write can't crash.
    """
    path = Path(transcript_path)
    if not path.exists():
        return False
    entries: list[dict] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    entries.append(parsed)
    except OSError:
        return False
    start = offset if isinstance(offset, int) and 0 <= offset <= len(entries) else 0
    return any(_entry_has_assistant_output(e) for e in entries[start:])


def _count_transcript_lines(path: Path) -> int:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _format_signal_lines(signals: dict[str, Any]) -> list[str]:
    """Format the token / tool / turn counters from ``signals.json``."""
    if not signals:
        return ["- signals: unavailable"]
    lines: list[str] = []
    used = _as_int(signals.get("contextTokensUsed"))
    total = _as_int(signals.get("contextWindowTokens"))
    if used is not None and total and total > 0:
        pct = (used / total) * 100
        lines.append(f"- context window: `{used:,}` / `{total:,}` ({pct:.1f}%)")
    elif used is not None:
        lines.append(f"- context tokens used: `{used:,}`")

    turns = _as_int(signals.get("turnCount"))
    tools = _as_int(signals.get("toolCallCount"))
    if turns is not None or tools is not None:
        lines.append(
            f"- turns: `{turns if turns is not None else '?'}`, "
            f"tool calls: `{tools if tools is not None else '?'}`"
        )
    tools_used = signals.get("toolsUsed")
    if isinstance(tools_used, list) and tools_used:
        names = ", ".join(str(t) for t in tools_used[:8])
        lines.append(f"- tools used: `{names}`")
    return lines or ["- signals: unavailable"]


def build_grok_status_snapshot(
    transcript_path: str,
    *,
    display_name: str,
    session_id: str = "",
    cwd: str = "",
) -> str | None:
    """Build a status snapshot from a Grok session directory.

    ``transcript_path`` points at ``chat_history.jsonl``; ``summary.json`` and
    ``signals.json`` live in the same directory.  Returns None when the session
    directory has no readable metadata or transcript.
    """
    path = Path(transcript_path)
    session_dir = path.parent
    summary = _read_json(session_dir / "summary.json")
    signals = _read_json(session_dir / "signals.json")
    transcript_lines = _count_transcript_lines(path)

    if not summary and transcript_lines == 0:
        return None

    info = _as_dict(summary.get("info"))
    resolved_session = (
        session_id
        or (info.get("id") if isinstance(info.get("id"), str) else "")
        or "unknown"
    )
    resolved_cwd = (
        cwd
        or (info.get("cwd") if isinstance(info.get("cwd"), str) else "")
        or "unknown"
    )
    model = (
        summary.get("current_model_id")
        if isinstance(summary.get("current_model_id"), str)
        else "unknown"
    )
    effort = summary.get("reasoning_effort")
    title = summary.get("generated_title") or summary.get("session_summary") or ""

    lines = [
        f"[{display_name}] Grok status snapshot",
        f"- session: `{resolved_session}`",
        f"- cwd: `{_display_cwd(resolved_cwd)}`",
        f"- model: `{model}`"
        + (f" (effort `{effort}`)" if isinstance(effort, str) and effort else ""),
    ]
    if isinstance(title, str) and title.strip():
        lines.append(f"- title: {title.strip()}")
    num_chat = _as_int(summary.get("num_chat_messages"))
    if num_chat is not None:
        lines.append(f"- chat messages: `{num_chat:,}`")
    lines.append(f"- transcript lines: `{transcript_lines:,}`")
    updated = summary.get("last_active_at") or summary.get("updated_at")
    if isinstance(updated, str) and updated:
        lines.append(f"- last active: `{updated}`")
    lines.extend(_format_signal_lines(signals))
    lines.append(
        "_Note: Grok `/session-info` renders in-TUI; this is a transcript snapshot._"
    )
    return "\n".join(lines)
