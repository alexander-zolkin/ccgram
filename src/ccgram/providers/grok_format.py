"""Grok Build transcript formatting — parse ``chat_history.jsonl`` lines.

Grok stores the raw chat messages sent to the model as newline-delimited JSON
in ``chat_history.jsonl``.  Each line is a flat object keyed by ``type``:

  - ``system``            — the system prompt (never relayed)
  - ``user``              — a human turn; ``content`` is a string or a list of
                            ``{type: text, text}`` blocks.  Setup turns carry a
                            ``synthetic_reason`` key or wrap ``<user_info>`` /
                            ``<system-reminder>`` blobs; the real prompt carries
                            ``prompt_index`` and is wrapped in ``<user_query>``.
  - ``reasoning``         — chain-of-thought.  ``summary`` is a list of
                            ``{type: summary_text, text}``; ``encrypted_content``
                            is an opaque blob that is NEVER relayed.
  - ``assistant``         — model turn.  ``content`` is a string (possibly empty)
                            plus an optional ``tool_calls`` array of
                            ``{id, name, arguments}`` (arguments is a JSON string).
  - ``backend_tool_call`` — server-side tool (web_search / web_fetch …) with a
                            nested ``kind`` describing the action.
  - ``tool_result``       — result of a client tool call, resolved back to the
                            call via ``tool_call_id``.

Parsers return ``(messages, pending)`` tuples so callers can chain pending-tool
state across batches exactly like the Claude/Codex/Pi providers.  ``pending``
maps ``tool_call_id -> (raw_name, display_name)``.
"""

from __future__ import annotations

import json
from typing import Any

from ccgram.expandable_quote import format_expandable_quote
from ccgram.providers.base import AgentMessage
from ccgram.tool_format import format_tool_line

# Grok exposes Claude-style tools under snake_case native names; ccgram's UI
# expects the title-case canonical names.  Mirror the alias table documented in
# Grok's hooks guide (Bash → run_terminal_command, etc.), reversed.
_TOOL_NAME_ALIASES: dict[str, str] = {
    "run_terminal_command": "Bash",
    "bash": "Bash",
    "read_file": "Read",
    "search_replace": "Edit",
    "create_file": "Write",
    "write_file": "Write",
    "delete_file": "Delete",
    "grep": "Grep",
    "list_dir": "List",
    "glob_file_search": "Glob",
    "web_search": "WebSearch",
    "web_fetch": "WebFetch",
    "spawn_subagent": "Task",
    "todo_write": "TodoWrite",
    "update_plan": "UpdatePlan",
}

# Line count above which a tool result is collapsed into "N lines" + an
# expandable quote instead of being inlined.
_TOOL_RESULT_QUOTE_THRESHOLD = 3

# Setup / synthetic user blocks that must never be relayed as a human turn.
_SYNTHETIC_USER_PREFIXES = ("<user_info", "<system-reminder", "<environment")

Pending = dict[str, tuple[str, str]]


def canonical_tool_name(name: str) -> str:
    """Map a Grok native tool name to a display-friendly canonical name."""
    return _TOOL_NAME_ALIASES.get(name.lower(), name)


def extract_text(content: Any) -> str:
    """Collect visible text from a Grok ``content`` field (string or blocks)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _strip_user_query_wrapper(text: str) -> str:
    """Unwrap the ``<user_query>…</user_query>`` envelope Grok adds to prompts."""
    stripped = text.strip()
    if stripped.startswith("<user_query>") and stripped.endswith("</user_query>"):
        inner = stripped[len("<user_query>") : -len("</user_query>")]
        return inner.strip()
    return text


def _is_synthetic_user_text(text: str) -> bool:
    """Return True for setup blobs (user_info, system-reminder) that aren't turns."""
    head = text.lstrip().lower()
    return head.startswith(_SYNTHETIC_USER_PREFIXES)


def normalize_pending(value: Any) -> Pending:
    """Coerce the cross-batch pending dict into the ``(raw, display)`` shape."""
    out: Pending = {}
    if not isinstance(value, dict):
        return out
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        if isinstance(item, (tuple, list)) and len(item) == 2:  # noqa: PLR2004
            raw, display = item
            if isinstance(raw, str) and isinstance(display, str):
                out[key] = (raw, display)
        elif isinstance(item, str):
            out[key] = (item, canonical_tool_name(item))
    return out


def parse_user(entry: dict[str, Any], timestamp: str | None) -> list[AgentMessage]:
    """Render a Grok ``user`` line, skipping synthetic setup turns."""
    if entry.get("synthetic_reason"):
        return []
    text = extract_text(entry.get("content", ""))
    if not text.strip():
        return []
    if _is_synthetic_user_text(text):
        return []
    text = _strip_user_query_wrapper(text).strip()
    if not text:
        return []
    return [
        AgentMessage(text=text, role="user", content_type="text", timestamp=timestamp)
    ]


def parse_reasoning(entry: dict[str, Any], timestamp: str | None) -> list[AgentMessage]:
    """Relay the plaintext reasoning *summary* only — never ``encrypted_content``."""
    summary = entry.get("summary")
    if not isinstance(summary, list):
        return []
    parts: list[str] = []
    for block in summary:
        if isinstance(block, dict) and block.get("type") == "summary_text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    if not parts:
        return []
    return [
        AgentMessage(
            text="\n".join(parts),
            role="assistant",
            content_type="thinking",
            timestamp=timestamp,
        )
    ]


def _tool_call_summary(raw_name: str, arguments: Any) -> str:
    """Pick a short, recognisable one-line summary for a tool call."""
    display = canonical_tool_name(raw_name)
    args = arguments
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError, TypeError:
            args = {}
    if not isinstance(args, dict):
        return format_tool_line(display, "")

    preferred_keys = (
        "command",
        "file_path",
        "target_file",
        "path",
        "target_directory",
        "pattern",
        "query",
        "url",
    )
    for key in preferred_keys:
        value = args.get(key)
        if isinstance(value, str) and value:
            return format_tool_line(display, value)
    for value in args.values():
        if isinstance(value, str) and value:
            return format_tool_line(display, value)
    return format_tool_line(display, "")


def parse_assistant(
    entry: dict[str, Any], pending: Pending, timestamp: str | None
) -> tuple[list[AgentMessage], Pending]:
    """Split an assistant line into text + tool_use AgentMessages (in order)."""
    messages: list[AgentMessage] = []
    text = extract_text(entry.get("content", "")).strip()
    if text:
        messages.append(
            AgentMessage(
                text=text,
                role="assistant",
                content_type="text",
                timestamp=timestamp,
            )
        )
    tool_calls = entry.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            raw_name = call.get("name")
            raw_name = raw_name if isinstance(raw_name, str) else "unknown"
            display = canonical_tool_name(raw_name)
            call_id = call.get("id")
            call_id = call_id if isinstance(call_id, str) else ""
            if call_id:
                pending[call_id] = (raw_name, display)
            messages.append(
                AgentMessage(
                    text=_tool_call_summary(raw_name, call.get("arguments", {})),
                    role="assistant",
                    content_type="tool_use",
                    tool_use_id=call_id or None,
                    tool_name=display,
                    timestamp=timestamp,
                )
            )
    return messages, pending


def parse_backend_tool_call(
    entry: dict[str, Any], timestamp: str | None
) -> list[AgentMessage]:
    """Render a server-side tool call (web_search / web_fetch …) compactly."""
    kind = entry.get("kind")
    if not isinstance(kind, dict):
        return []
    tool_type = kind.get("tool_type")
    tool_type = tool_type if isinstance(tool_type, str) else "backend_tool"
    display = canonical_tool_name(tool_type)
    action = kind.get("action")
    summary = ""
    if isinstance(action, dict):
        for key in ("query", "url", "prompt"):
            value = action.get(key)
            if isinstance(value, str) and value:
                summary = value
                break
    return [
        AgentMessage(
            text=format_tool_line(display, summary),
            role="assistant",
            content_type="tool_use",
            tool_name=display,
            timestamp=timestamp,
        )
    ]


def _format_tool_result_text(raw_name: str, output: str) -> str:
    """Long outputs → ``N lines`` + expandable quote; short ones stay inline."""
    if not output:
        return "Done"
    line_count = output.count("\n") + 1
    is_shell = canonical_tool_name(raw_name) == "Bash"
    if is_shell or line_count > _TOOL_RESULT_QUOTE_THRESHOLD:
        unit = "line" if line_count == 1 else "lines"
        stats = f"  ⎿  {line_count} {unit}"
        return stats + "\n" + format_expandable_quote(output)
    return output


def parse_tool_result(
    entry: dict[str, Any], pending: Pending, timestamp: str | None
) -> tuple[list[AgentMessage], Pending]:
    """Resolve a ``tool_result`` line back to its call via ``tool_call_id``."""
    call_id = entry.get("tool_call_id")
    call_id = call_id if isinstance(call_id, str) else ""
    raw_name, display = "unknown", "unknown"
    if call_id and call_id in pending:
        raw_name, display = pending.pop(call_id)

    output = extract_text(entry.get("content", "")).strip()
    is_error = bool(entry.get("is_error") or entry.get("isError"))
    if is_error and output:
        text = f"Error: {output}"
    elif is_error:
        text = "Error"
    else:
        text = _format_tool_result_text(raw_name, output)

    return (
        [
            AgentMessage(
                text=text,
                role="assistant",
                content_type="tool_result",
                tool_use_id=call_id or None,
                tool_name=display,
                timestamp=timestamp,
            )
        ],
        pending,
    )


def parse_chat_entry(
    entry: dict[str, Any], pending: Pending, timestamp: str | None = None
) -> tuple[list[AgentMessage], Pending]:
    """Dispatch one ``chat_history.jsonl`` line to the type-specific parser."""
    etype = entry.get("type", "")
    if etype == "user":
        return parse_user(entry, timestamp), pending
    if etype == "assistant":
        return parse_assistant(entry, pending, timestamp)
    if etype == "reasoning":
        return parse_reasoning(entry, timestamp), pending
    if etype == "backend_tool_call":
        return parse_backend_tool_call(entry, timestamp), pending
    if etype == "tool_result":
        return parse_tool_result(entry, pending, timestamp)
    # system and any unknown line types produce no relay output.
    return [], pending
