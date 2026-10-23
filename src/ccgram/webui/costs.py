"""What the Claude Code sessions would have cost at API prices.

On a subscription nothing is billed per token; this is the value of the work —
the same figure Claude Code's /usage shows as a session's "Total cost". Every
API message's usage is in the transcript (repeated on each content block, so
counted once per message id); prices are per million tokens. A chat's numbers
include its subagents (``<session>/subagents/*.jsonl``). The 30-day overview
walks every project's transcripts except the private root's — a full pass
takes a couple of seconds, so it is cached for a few minutes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# $ per million tokens: input, output, cache read, cache write 5 min, 1 h.
PRICES: dict[str, tuple[float, float, float, float, float]] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5, 20.0),
    "claude-mythos-5-1": (10.0, 50.0, 0.25, 12.5, 20.0),
    "claude-fable-5": (10.0, 50.0, 1.0, 12.5, 20.0),
    "claude-mythos-5": (10.0, 50.0, 1.0, 12.5, 20.0),
    "claude-opus-5-5": (4.0, 20.0, 0.2, 5.0, 8.0),
    "claude-opus-5": (5.0, 25.0, 0.5, 6.25, 10.0),
    "claude-opus-4": (5.0, 25.0, 0.5, 6.25, 10.0),
    "claude-sonnet-5": (2.0, 10.0, 0.2, 2.5, 4.0),
    "claude-sonnet-4": (3.0, 15.0, 0.3, 3.75, 6.0),
    "claude-haiku-5-5": (0.1, 0.5, 0.01, 0.125, 0.2),
    "claude-haiku-4": (1.0, 5.0, 0.1, 1.25, 2.0),
}
FAST_FACTOR = 2.0  # fast mode bills double
PERIOD_DAYS = 30
PERIOD_TTL_S = 180.0
TOP_CHATS = 6


def price(model: str) -> tuple[float, float, float, float, float] | None:
    base = model.split("[", 1)[0]
    best = max((k for k in PRICES if base.startswith(k)), key=len, default=None)
    return PRICES[best] if best else None


def _usage_parts(usage: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """(input, output, cache read, cache write 5 min, cache write 1 h)."""
    split = usage.get("cache_creation") or {}
    w5 = int(split.get("ephemeral_5m_input_tokens") or 0)
    w1 = int(split.get("ephemeral_1h_input_tokens") or 0)
    if not (w5 or w1):
        w5 = int(usage.get("cache_creation_input_tokens") or 0)
    return (
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
        int(usage.get("cache_read_input_tokens") or 0),
        w5,
        w1,
    )


class Tally:
    """Tokens and dollars per model."""

    def __init__(self) -> None:
        self.models: dict[str, list[float]] = {}  # in, out, read, write, $

    def add(self, model: str, usage: dict[str, Any]) -> float:
        parts = _usage_parts(usage)
        rates = price(model)
        cost = 0.0
        if rates:
            cost = sum(n * r for n, r in zip(parts, rates, strict=True)) / 1e6
            if usage.get("speed") == "fast":
                cost *= FAST_FACTOR
        row = self.models.setdefault(model.split("[", 1)[0], [0.0] * 5)
        for i, n in enumerate((parts[0], parts[1], parts[2], parts[3] + parts[4])):
            row[i] += n
        row[4] += cost
        return cost

    def merge(self, other: Tally) -> None:
        for model, row in other.models.items():
            mine = self.models.setdefault(model, [0.0] * 5)
            for i, v in enumerate(row):
                mine[i] += v

    def summary(self) -> dict[str, Any]:
        rows = sorted(self.models.items(), key=lambda kv: -kv[1][4] or -kv[1][1])
        total = [sum(r[i] for _m, r in rows) for i in range(5)]
        fed = total[0] + total[2] + total[3]
        return {
            "cost": round(total[4], 4),
            "input": int(total[0]),
            "output": int(total[1]),
            "cache_read": int(total[2]),
            "cache_write": int(total[3]),
            "cache_hit": round(total[2] / fed, 3) if fed else None,
            "models": [
                {
                    "model": m,
                    "input": int(r[0]),
                    "output": int(r[1]),
                    "cache_read": int(r[2]),
                    "cache_write": int(r[3]),
                    "cost": round(r[4], 4),
                    "priced": price(m) is not None,
                }
                for m, r in rows
            ],
        }


def _epoch(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _patch_lines(result: Any) -> tuple[int, int]:
    """Lines added / removed by an Edit / Write tool result."""
    if not isinstance(result, dict):
        return 0, 0
    added = removed = 0
    patch = result.get("structuredPatch")
    if isinstance(patch, list) and patch:
        for hunk in patch:
            for line in (hunk or {}).get("lines") or []:
                if isinstance(line, str) and line.startswith("+"):
                    added += 1
                elif isinstance(line, str) and line.startswith("-"):
                    removed += 1
    elif result.get("type") == "create" and isinstance(result.get("content"), str):
        added = len(result["content"].splitlines())
    return added, removed


def _entries(path: Path):
    try:
        fh = path.open("rb")
    except OSError:
        return
    with fh:
        for raw in fh:
            if not (
                b'"usage"' in raw
                or b"turn_duration" in raw
                or b"structuredPatch" in raw
            ):
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if isinstance(entry, dict):
                yield entry


def _files_of(path: Path) -> list[Path]:
    """A session's transcript and its subagents'."""
    subs = path.with_suffix("") / "subagents"
    return [path, *sorted(subs.glob("*.jsonl"))] if subs.is_dir() else [path]


def _is_turn_end(entry: dict[str, Any]) -> bool:
    return entry.get("type") == "system" and entry.get("subtype") == "turn_duration"


def _priced_message(entry: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    """(message id, model, usage) of an assistant entry worth counting."""
    msg = entry.get("message")
    if entry.get("type") != "assistant" or not isinstance(msg, dict):
        return None
    usage, model = msg.get("usage"), str(msg.get("model") or "")
    if not isinstance(usage, dict) or not model or model == "<synthetic>":
        return None
    return str(msg.get("id") or entry.get("uuid") or ""), model, usage


def chat_stats(path: Path) -> dict[str, Any]:
    """One chat: cost and tokens by model, turns, time, code changed."""
    tally, seen = Tally(), set()
    turns, busy_ms, added, removed = 0, 0, 0, 0
    stamps: list[float] = []
    for i, file in enumerate(_files_of(path)):
        main = i == 0  # subagents add cost, not turns or wall time
        for entry in _entries(file):
            when = _epoch(entry.get("timestamp"))
            if main and when is not None:
                stamps.append(when)
            if _is_turn_end(entry):
                turns += main
                busy_ms += int(entry.get("durationMs") or 0) if main else 0
                continue
            plus, minus = _patch_lines(entry.get("toolUseResult"))
            added, removed = added + plus, removed + minus
            priced = _priced_message(entry)
            if priced is not None and priced[0] not in seen:
                seen.add(priced[0])
                tally.add(priced[1], priced[2])
    return {
        **tally.summary(),
        "turns": turns,
        "busy_s": round(busy_ms / 1000),
        "wall_s": round(max(stamps) - min(stamps)) if stamps else 0,
        "lines_added": added,
        "lines_removed": removed,
        "subagents": len(_files_of(path)) - 1,
    }


# ── the last 30 days across every (non-private) session ──────────────────────


def _project_dirs(private_root: Path) -> list[Path]:
    base = Path(os.getenv("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"))
    hidden = "-" + str(private_root).strip("/").replace("/", "-").replace(".", "-")
    try:
        dirs = [d for d in (base / "projects").iterdir() if d.is_dir()]
    except OSError:
        return []
    return [d for d in dirs if not d.name.startswith(hidden)]


class _Period:
    """30 days of every session, one transcript at a time."""

    def __init__(self) -> None:
        self.today = date.today().isoformat()
        first = date.today() - timedelta(days=PERIOD_DAYS - 1)
        self.since = datetime.combine(first, datetime.min.time()).timestamp()
        self.week_from = (date.today() - timedelta(days=6)).isoformat()
        self.days = {
            (first + timedelta(days=n)).isoformat(): {"cost": 0.0, "out": 0, "turns": 0}
            for n in range(PERIOD_DAYS)
        }
        self.models, self.week_models = Tally(), Tally()
        self.chats: dict[str, float] = {}
        self.sessions: dict[str, set[str]] = {
            "today": set(),
            "week": set(),
            "month": set(),
        }

    def add_file(self, file: Path, sid: str) -> None:
        seen: set[str] = set()
        for entry in _entries(file):
            when = _epoch(entry.get("timestamp"))
            day = datetime.fromtimestamp(when).date().isoformat() if when else ""
            if day not in self.days:
                continue
            if _is_turn_end(entry):
                self.days[day]["turns"] += 1
                continue
            priced = _priced_message(entry)
            if priced is None or priced[0] in seen:
                continue
            seen.add(priced[0])
            self._count(day, sid, priced[1], priced[2])

    def _count(self, day: str, sid: str, model: str, usage: dict[str, Any]) -> None:
        cost = self.models.add(model, usage)
        self.days[day]["cost"] += cost
        self.days[day]["out"] += int(usage.get("output_tokens") or 0)
        self.sessions["month"].add(sid)
        if day >= self.week_from:
            self.week_models.add(model, usage)
            self.sessions["week"].add(sid)
            self.chats[sid] = self.chats.get(sid, 0.0) + cost
        if day == self.today:
            self.sessions["today"].add(sid)

    def result(self, names: dict[str, str]) -> dict[str, Any]:
        rows = [
            {"date": d, **v, "cost": round(v["cost"], 4)} for d, v in self.days.items()
        ]

        def window(n: int, key: str) -> dict[str, Any]:
            part = rows[-n:]
            return {
                "cost": round(sum(r["cost"] for r in part), 2),
                "out": sum(r["out"] for r in part),
                "turns": sum(r["turns"] for r in part),
                "sessions": len(self.sessions[key]),
            }

        named = sorted(
            ((names[s], c) for s, c in self.chats.items() if s in names),
            key=lambda x: -x[1],
        )
        rest = sum(c for s, c in self.chats.items() if s not in names)
        rest += sum(c for _n, c in named[TOP_CHATS:])
        return {
            "days": rows,
            "today": window(1, "today"),
            "week": window(7, "week"),
            "month": window(PERIOD_DAYS, "month"),
            "models": self.models.summary()["models"],
            "week_models": self.week_models.summary()["models"],
            "chats": [{"name": n, "cost": round(c, 2)} for n, c in named[:TOP_CHATS]],
            "chats_rest": round(rest, 2),
            "as_of": time.time(),
        }


def period_stats(private_root: Path, names: dict[str, str]) -> dict[str, Any]:
    """Cost per day for 30 days, windows (today / 7 / 30 days), models, chats.

    ``names`` maps a session id to its chat name; other sessions (SSH, cron
    jobs, headless helpers) are summed as one "other" row.
    """
    period = _Period()
    for project in _project_dirs(private_root):
        for file in project.glob("**/*.jsonl"):
            try:
                if file.stat().st_mtime < period.since:
                    continue
            except OSError:
                continue
            # subagents/agent-*.jsonl belong to the session dir above them
            sub = file.parent.name == "subagents"
            period.add_file(file, file.parent.parent.name if sub else file.stem)
    return period.result(names)


# ── what the statistics sheet asks for ───────────────────────────────────────

_period: tuple[float, dict[str, Any] | None] = (0.0, None)
_period_lock = asyncio.Lock()


async def _period_cached() -> dict[str, Any] | None:
    global _period
    # Lazy: service pulls the handler graph.
    from . import service

    async with _period_lock:
        stamp, data = _period
        if data is not None and time.time() - stamp < PERIOD_TTL_S:
            return data
        names: dict[str, str] = {}
        for s in await service.list_sessions():  # private chats are not listed
            with contextlib.suppress(service.WebUIError):
                tx, _ = service._transcript_for(service.find_binding(s["thread_id"]))
                if tx:
                    names[tx.stem] = s["name"]
        data = await asyncio.to_thread(period_stats, service._private_root(), names)
        _period = (time.time(), data)
        return data


async def _chat(thread_id: int) -> dict[str, Any] | None:
    # Lazy: service pulls the handler graph.
    from ..config import config
    from . import service

    binding = service.find_binding(thread_id)
    if not config.webui_show_private and service._is_private_cwd(
        service._cwd_for(binding)
    ):
        return None
    tx, _ = service._transcript_for(binding)
    return await asyncio.to_thread(chat_stats, tx) if tx and tx.exists() else None


async def overview(thread_id: int | None) -> dict[str, Any]:
    """Plan limits + credits, this chat, the last 30 days."""
    # Lazy: usage pulls nothing heavy, but keep the pair side by side.
    from . import usage

    plan, chat, period = await asyncio.gather(
        usage.plan_usage(),
        _chat(thread_id) if thread_id is not None else asyncio.sleep(0),
        _period_cached(),
    )
    return {"usage": plan, "chat": chat, "period": period}


def reset_for_testing() -> None:
    global _period
    _period = (0.0, None)
