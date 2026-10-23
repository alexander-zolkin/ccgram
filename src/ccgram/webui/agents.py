"""Subagents of a Claude session, for the web UI.

Claude Code keeps each subagent's conversation next to the session's own
transcript: ``<project>/<session>/subagents/agent-<id>.jsonl``, with
``agent-<id>.meta.json`` naming the parent's ``Agent`` tool call
(``toolUseId``), the description and the agent type. Lines there are
``isSidechain`` entries; the page reads them through the same item shapes as
the main thread.

Stats per agent come from its own lines, cached by (size, mtime): actions
(tool calls), output tokens (per API message id, like the turn footer),
first/last timestamps, model, the latest action, and whether it has
finished (its last entry is a final answer) or still runs.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import transcript

# A subagent that wrote nothing for this long without finishing has stopped
# (killed with its session, or interrupted).
STALE_S = 15 * 60
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_FINAL = ("end_turn", "stop_sequence", "max_tokens", "refusal")

_cache: dict[Path, tuple[tuple[int, float], dict[str, Any]]] = {}


def agents_dir(session_transcript: Path) -> Path:
    return session_transcript.with_suffix("") / "subagents"


def agent_path(session_transcript: Path, agent_id: str) -> Path | None:
    if not _ID.match(agent_id):
        return None
    path = agents_dir(session_transcript) / f"agent-{agent_id}.jsonl"
    return path if path.is_file() else None


def _epoch(ts: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _action(block: dict[str, Any]) -> str:
    """A short line for a tool call: «Bash: ls -la», «Read: app.js»."""
    name = str(block.get("name") or "")
    raw = block.get("input") or {}
    what = ""
    for key in ("description", "command", "file_path", "pattern", "query", "url"):
        if isinstance(raw.get(key), str) and raw[key].strip():
            what = raw[key].strip().split("\n")[0]
            if key == "file_path":
                what = Path(what).name
            break
    return f"{name}: {what[:120]}" if what else name


class _Scan:
    def __init__(self) -> None:
        self.tools: set[str] = set()
        self.out: dict[str, int] = {}
        self.first: float | None = None
        self.last: float | None = None
        self.model = ""
        self.action = ""
        self.final = False
        self.errors = 0

    def add(self, entry: dict[str, Any]) -> None:
        when = _epoch(entry.get("timestamp"))
        if when is not None:
            self.first = when if self.first is None else min(self.first, when)
            self.last = when if self.last is None else max(self.last, when)
        kind = entry.get("type")
        msg = entry.get("message") or {}
        if kind == "user":
            self.final = False  # a continuation (SendMessage) or tool output
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("is_error"):
                    self.errors += 1
            return
        if kind != "assistant" or not isinstance(msg, dict):
            return
        self._assistant(msg, entry)

    def _assistant(self, msg: dict[str, Any], entry: dict[str, Any]) -> None:
        if msg.get("model") and msg["model"] != "<synthetic>":
            self.model = msg["model"]
        usage = msg.get("usage") or {}
        mid = msg.get("id") or entry.get("uuid", "")
        self.out[mid] = max(self.out.get(mid, 0), int(usage.get("output_tokens") or 0))
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                self.tools.add(str(block.get("id") or len(self.tools)))
                self.action = _action(block)
            elif block.get("type") == "text" and (block.get("text") or "").strip():
                self.action = block["text"].strip().split("\n")[0][:160]
        self.final = msg.get("stop_reason") in _FINAL


def _scan(path: Path) -> dict[str, Any]:
    st = path.stat()
    sig = (st.st_size, st.st_mtime)
    hit = _cache.get(path)
    if hit and hit[0] == sig:
        return hit[1]
    scan = _Scan()
    with path.open("rb") as fh:
        for raw in fh:
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if isinstance(entry, dict):
                scan.add(entry)
    stats = {
        "tools": len(scan.tools),
        "out": sum(scan.out.values()),
        "started": scan.first,
        "last": scan.last,
        "model": scan.model,
        "action": scan.action,
        "final": scan.final,
        "errors": scan.errors,
        "mtime": st.st_mtime,
    }
    _cache[path] = (sig, stats)
    return stats


def _meta(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def list_agents(session_transcript: Path) -> list[dict[str, Any]]:
    """Every subagent of the session, oldest first, with live stats."""
    folder = agents_dir(session_transcript)
    if not folder.is_dir():
        return []
    now = time.time()
    out = []
    for path in folder.glob("agent-*.jsonl"):
        try:
            stats = _scan(path)
        except OSError:
            continue
        meta = _meta(path)
        if stats["final"]:
            status = "done"
        elif now - stats["mtime"] < STALE_S:
            status = "running"
        else:
            status = "stopped"
        out.append(
            {
                "id": path.stem.removeprefix("agent-"),
                "tool_id": meta.get("toolUseId") or "",
                "description": meta.get("description") or "",
                "type": meta.get("agentType") or "",
                "background": meta.get("requestShape") == "background",
                "status": status,
                **{k: v for k, v in stats.items() if k not in ("final", "mtime")},
            }
        )
    out.sort(key=lambda a: a["started"] or 0)
    return out


def entries(
    session_transcript: Path,
    agent_id: str,
    *,
    limit: int,
    before: int | None = None,
    after: int | None = None,
) -> dict[str, Any] | None:
    """A subagent's own thread, paged like the session's (byte cursors)."""
    path = agent_path(session_transcript, agent_id)
    if path is None:
        return None
    if after is not None:
        return transcript.read_after(path, after, sidechain=True)
    return transcript.read_tail(path, limit, before, sidechain=True)
