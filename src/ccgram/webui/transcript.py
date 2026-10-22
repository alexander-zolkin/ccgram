"""Rich transcript items for the web UI, read straight from Claude's JSONL.

ccgram's own parser renders for Telegram (flattened tool lines, markdown
already spent on emoji). The browser can do better, so for Claude sessions
this module reads the raw entries and keeps their structure: tool inputs,
outputs, error flags and the structured patch of every edit.

Reads are byte-cursor based so the page can page backwards (``before``) and
poll forwards (``after``) without re-reading a 100 MB transcript:

- :func:`read_tail` — the last ``limit`` items, plus ``start``/``end`` offsets
- :func:`read_after` — items in complete lines past ``after``

A tool result that lands in a later read than its call is emitted as a
standalone ``tool_result`` item; the client merges it by ``id``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

CHUNK = 1024 * 1024
MAX_SCAN = 24 * 1024 * 1024
MAX_FIELD = 6000
MAX_PATCH_LINES = 400
MAX_STRUCT = 4000

# User entries that are harness plumbing, not something the person typed.
_PLUMBING = re.compile(
    r"^\s*<(command-name|command-message|command-args|local-command-stdout|"
    r"local-command-stderr|system-reminder|task-notification|bash-input|"
    r"bash-stdout|bash-stderr|user-memory-input)\b"
)
_SYNTHETIC_CONTINUE = "Continue from where you left off."
_TAG = re.compile(
    r"<(bash-input|bash-stdout|bash-stderr|command-name|command-args|local-command-stdout)>(.*?)</\1>",
    re.S,
)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _tags(text: str) -> dict[str, str]:
    return {m.group(1): _ANSI.sub("", m.group(2)) for m in _TAG.finditer(text)}


def _special_user(text: str, ts: Any, uuid: str) -> list[dict[str, Any]] | None:
    """``!`` shell runs and slash commands as their own items (None = not one)."""
    tags = _tags(text)
    if "bash-input" in tags:
        return [
            {
                "kind": "bash",
                "command": tags["bash-input"].strip(),
                "ts": ts,
                "uuid": uuid,
            }
        ]
    if "bash-stdout" in tags or "bash-stderr" in tags:
        return [
            {
                "kind": "bash_result",
                "stdout": _clip(tags.get("bash-stdout", "").rstrip(), MAX_FIELD * 3),
                "stderr": _clip(tags.get("bash-stderr", "").rstrip(), MAX_FIELD),
                "ts": ts,
                "uuid": uuid,
            }
        ]
    if "command-name" in tags:
        name = tags["command-name"].strip()
        args = tags.get("command-args", "").strip()
        return [
            {
                "kind": "command",
                "text": f"{name} {args}".strip(),
                "ts": ts,
                "uuid": uuid,
            }
        ]
    if "local-command-stdout" in tags:
        out = tags["local-command-stdout"].strip()
        return (
            [{"kind": "command_result", "text": _clip(out), "ts": ts, "uuid": uuid}]
            if out
            else []
        )
    return None


def _clip(text: Any, limit: int = MAX_FIELD) -> str:
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + f"\n… (+{len(s) - limit} chars)"


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "image":
                parts.append("[image]")
        return "\n".join(parts)
    return ""


def _tool_input(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Keep the fields the UI renders; clip anything large."""
    keep: dict[str, Any] = {}
    for key, value in raw.items():
        if key in ("old_string", "new_string") and name in ("Edit", "MultiEdit"):
            continue  # the result's structuredPatch shows the change better
        if key == "content" and name == "Write":
            keep[key] = _clip(value, 3000)
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            keep[key] = _clip(value, 2000) if isinstance(value, str) else value
        else:
            # Small structures (todos, questions) stay structured for the UI.
            dumped = json.dumps(value, ensure_ascii=False)
            keep[key] = (
                value if len(dumped) <= MAX_STRUCT else _clip(dumped, MAX_STRUCT)
            )
    return keep


def _patch(tur: Any) -> list[dict[str, Any]] | None:
    if not isinstance(tur, dict):
        return None
    hunks = tur.get("structuredPatch")
    if not isinstance(hunks, list) or not hunks:
        return None
    out, budget = [], MAX_PATCH_LINES
    for h in hunks:
        if not isinstance(h, dict) or budget <= 0:
            break
        lines = [str(x) for x in (h.get("lines") or [])][:budget]
        budget -= len(lines)
        out.append(
            {
                "old_start": h.get("oldStart"),
                "new_start": h.get("newStart"),
                "lines": lines,
            }
        )
    return out


def _result_item(block: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    tur = entry.get("toolUseResult")
    item: dict[str, Any] = {
        "kind": "tool_result",
        "id": block.get("tool_use_id", ""),
        "is_error": bool(block.get("is_error")),
        "text": _clip(_result_text(block.get("content"))),
    }
    if isinstance(tur, dict):
        if "stdout" in tur or "stderr" in tur:
            item["stdout"] = _clip(tur.get("stdout") or "")
            item["stderr"] = _clip(tur.get("stderr") or "")
            item["interrupted"] = bool(tur.get("interrupted"))
        patch = _patch(tur)
        if patch:
            item["patch"] = patch
    return item


def _parse_user_text(
    entry: dict[str, Any], content: str, ts: Any, uuid: str
) -> list[dict[str, Any]]:
    if entry.get("isCompactSummary"):
        text = _clip(content, MAX_FIELD * 4)
        return [{"kind": "summary", "text": text, "ts": ts, "uuid": uuid}]
    if _PLUMBING.match(content):
        special = _special_user(content, ts, uuid)
        return special if special is not None else []
    if content.strip() == _SYNTHETIC_CONTINUE:
        return []
    return [{"kind": "user", "text": content, "ts": ts, "uuid": uuid}]


def _parse_user(entry: dict[str, Any], content: Any, ts: Any, uuid: str) -> list:
    if entry.get("isMeta"):
        return []
    if isinstance(content, str):
        return _parse_user_text(entry, content, ts, uuid)
    if not isinstance(content, list):
        return []
    items: list[dict[str, Any]] = []
    texts, images = [], 0
    for block in content:
        btype = block.get("type") if isinstance(block, dict) else None
        if btype == "tool_result":
            items.append(_result_item(block, entry))
        elif btype == "text" and not _PLUMBING.match(block.get("text", "")):
            texts.append(block.get("text", ""))
        elif btype == "image":
            images += 1
    if texts or images:
        text = "\n".join(t for t in texts if t)
        items.append(
            {"kind": "user", "text": text, "images": images, "ts": ts, "uuid": uuid}
        )
    return items


def _parse_assistant(content: Any, ts: Any, uuid: str) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    items: list[dict[str, Any]] = []
    for idx, block in enumerate(content):
        btype = block.get("type") if isinstance(block, dict) else None
        if btype == "text" and (block.get("text") or "").strip():
            items.append(
                {
                    "kind": "assistant",
                    "text": block["text"],
                    "ts": ts,
                    "uuid": f"{uuid}:{idx}",
                }
            )
        elif btype == "thinking":
            items.append({"kind": "thinking", "ts": ts, "uuid": f"{uuid}:{idx}"})
        elif btype == "tool_use":
            name = block.get("name", "")
            items.append(
                {
                    "kind": "tool",
                    "id": block.get("id", ""),
                    "name": name,
                    "input": _tool_input(name, block.get("input") or {}),
                    "ts": ts,
                }
            )
    return items


def parse_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn one JSONL entry into zero or more UI items."""
    kind = entry.get("type")
    if kind not in ("user", "assistant") or entry.get("isSidechain"):
        return []
    message = entry.get("message") or {}
    content, ts, uuid = (
        message.get("content"),
        entry.get("timestamp"),
        entry.get("uuid", ""),
    )
    if kind == "user":
        return _parse_user(entry, content, ts, uuid)
    return _parse_assistant(content, ts, uuid)


def _merge_results(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold tool_result items into their tool call when both are present."""
    calls = {it["id"]: it for it in items if it["kind"] == "tool" and it.get("id")}
    out = []
    for it in items:
        if it["kind"] == "tool_result" and it["id"] in calls:
            calls[it["id"]]["result"] = {k: v for k, v in it.items() if k != "kind"}
            continue
        out.append(it)
    return out


def _parse_lines(lines: list[bytes]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in lines:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if isinstance(entry, dict):
            items.extend(parse_entry(entry))
    return items


def read_after(path: Path, after: int) -> dict[str, Any]:
    """Items from complete lines past byte ``after``; ``end`` is the new cursor."""
    size = path.stat().st_size
    if after >= size:
        return {"items": [], "end": max(after, 0) if after <= size else size}
    with open(path, "rb") as fh:
        fh.seek(max(after, 0))
        data = fh.read(min(size - after, MAX_SCAN))
    cut = data.rfind(b"\n")
    if cut < 0:
        return {"items": [], "end": after}
    complete = data[: cut + 1]
    items = _merge_results(_parse_lines(complete.splitlines()))
    return {"items": items, "end": after + len(complete)}


def read_tail(path: Path, limit: int, before: int | None = None) -> dict[str, Any]:
    """The last ``limit`` items ending at byte ``before`` (default: EOF).

    ``start`` is the byte where the returned window begins — pass it back as
    ``before`` to page further up; ``0`` means the top of the transcript.
    Reads backwards in chunks and parses each line once.
    """
    size = path.stat().st_size
    end = size if before is None else max(0, min(before, size))
    with open(path, "rb") as fh:
        if before is None and end > 0:
            # Only complete lines: a line still being written is picked up
            # by the next read_after from ``end``.
            back = min(end, 65536)
            fh.seek(end - back)
            tail = fh.read(back)
            cut = tail.rfind(b"\n")
            end = end - back + cut + 1 if cut >= 0 else end - back
        items: list[dict[str, Any]] = []
        carry = b""
        pos = start = end
        while pos > 0 and end - pos < MAX_SCAN and len(items) < limit:
            new = max(0, pos - CHUNK)
            fh.seek(new)
            data = fh.read(pos - new) + carry
            pos = new
            if new > 0:
                nl = data.find(b"\n")
                if nl < 0:
                    carry = data
                    continue
                carry, body, start = data[: nl + 1], data[nl + 1 :], new + nl + 1
            else:
                carry, body, start = b"", data, 0
            items = _parse_lines(body.splitlines()) + items
    merged = _merge_results(items)
    return {"items": merged[-limit:] if limit else merged, "start": start, "end": end}


def last_usage(path: Path, window: int = 512 * 1024) -> dict[str, Any] | None:
    """Model and context size from the newest assistant entry with usage."""
    try:
        with path.open("rb") as fh:
            size = fh.seek(0, 2)
            fh.seek(max(0, size - window))
            lines = fh.read().splitlines()
    except OSError:
        return None
    for raw in reversed(lines):
        if b'"usage"' not in raw or b'"assistant"' not in raw:
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        msg = entry.get("message") or {}
        usage = msg.get("usage") or {}
        if entry.get("type") != "assistant" or entry.get("isSidechain") or not usage:
            continue
        context = sum(
            int(usage.get(k) or 0)
            for k in (
                "input_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "output_tokens",
            )
        )
        return {"model": msg.get("model") or "", "context": context}
    return None


def search(path: Path, needle: str, limit: int = 3) -> list[dict[str, Any]]:
    """Up to ``limit`` user/assistant messages containing ``needle`` (lowercase)."""
    hits: list[dict[str, Any]] = []
    try:
        with path.open("rb") as fh:
            for raw in fh:
                # Cheap prefilter on the raw line before parsing JSON.
                if needle not in raw.decode("utf-8", "replace").lower():
                    continue
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                for it in parse_entry(entry):
                    text = (
                        it.get("text") if it["kind"] in ("user", "assistant") else None
                    )
                    if not text:
                        continue
                    low = text.lower()
                    at = low.find(needle)
                    if at < 0:
                        continue
                    start = max(0, at - 60)
                    snippet = text[start : at + len(needle) + 90].replace("\n", " ")
                    hits.append(
                        {
                            "role": it["kind"],
                            "snippet": ("…" if start else "") + snippet,
                            "ts": it.get("ts"),
                        }
                    )
                    if len(hits) >= limit:
                        return hits
    except OSError:
        pass
    return hits


def file_size(path: Path) -> int:
    try:
        return os.stat(path).st_size
    except OSError:
        return 0


def first_exchange(
    path: Path, scan: int = 2 * 1024 * 1024, max_user: int = 3
) -> tuple[list[str], str]:
    """(first ``max_user`` user texts, first assistant reply) from the head."""
    users: list[str] = []
    reply = ""
    try:
        with open(path, "rb") as fh:
            data = fh.read(scan)
    except OSError:
        return [], ""
    for line in data.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        for item in parse_entry(entry):
            if item["kind"] == "user" and item.get("text") and len(users) < max_user:
                users.append(item["text"])
            elif item["kind"] == "assistant" and users and not reply:
                reply = item["text"]
        if len(users) >= max_user and reply:
            break
    # A reply is optional: titles are made from the first message alone.
    return users, reply
