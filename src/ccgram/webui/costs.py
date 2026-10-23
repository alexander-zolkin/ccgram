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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

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
HEATMAP_MIN_WEEKS = 26


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
        rows = sorted(self.models.items(), key=lambda kv: (-kv[1][4], -kv[1][1]))
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


class _Day:
    __slots__ = ("costs", "tally", "turns")

    def __init__(self) -> None:
        self.tally = Tally()
        self.turns = 0
        self.costs: dict[str, float] = {}  # session → $ that day

    @property
    def out(self) -> int:
        return int(sum(r[1] for r in self.tally.models.values()))


class _Period:
    """Every (non-private) session, tallied per local day."""

    def __init__(self) -> None:
        self.days: dict[str, _Day] = {}
        self.spans: dict[str, list[float]] = {}  # session → [first, last]

    def add_file(
        self, entries: Iterable[dict[str, Any]], sid: str, main: bool, seen: set[str]
    ) -> None:
        for entry in entries:
            when = _epoch(entry.get("timestamp"))
            if when is None:
                continue
            day = self.days.setdefault(
                datetime.fromtimestamp(when).date().isoformat(), _Day()
            )
            if main:  # a session's length: its own transcript, not subagents'
                span = self.spans.setdefault(sid, [when, when])
                span[0], span[1] = min(span[0], when), max(span[1], when)
            if _is_turn_end(entry):
                day.turns += main
                continue
            priced = _priced_message(entry)
            if priced is None or priced[0] in seen:
                continue
            seen.add(priced[0])
            cost = day.tally.add(priced[1], priced[2])
            day.costs[sid] = day.costs.get(sid, 0.0) + cost

    def _window(self, first: date, last: date) -> dict[str, Any]:
        """Totals, streaks, favourite model and busiest day for a date range."""
        tally, sessions, turns = Tally(), set(), 0
        active, best, streak, run = 0, None, 0, 0
        n = (last - first).days + 1
        for i in range(n):
            key = (first + timedelta(days=i)).isoformat()
            day = self.days.get(key)
            if day and day.costs:
                tally.merge(day.tally)
                sessions |= day.costs.keys()
                active, run = active + 1, run + 1
                if best is None or day.out > best[1]:
                    best = (key, day.out)
            else:
                run = 0
            streak = max(streak, run)
            turns += day.turns if day else 0
        current = run or self._streak_before(last)
        summary = tally.summary()
        favorite = max(summary["models"], key=lambda m: m["output"], default=None)
        spans = [
            self.spans[s][1] - self.spans[s][0] for s in sessions if s in self.spans
        ]
        return {
            "cost": round(summary["cost"], 2),
            "input": summary["input"],
            "output": summary["output"],
            "cache_read": summary["cache_read"],
            "cache_write": summary["cache_write"],
            "turns": turns,
            "sessions": len(sessions),
            "longest_session_s": round(max(spans, default=0)),
            "active_days": active,
            "days": n,
            "longest_streak": streak,
            "current_streak": current,
            "busiest": {"date": best[0], "out": best[1]} if best else None,
            "favorite": favorite["model"] if favorite else None,
            "models": summary["models"],
        }

    def _streak_before(self, last: date) -> int:
        """Today without work yet doesn't break the streak that ended yesterday."""
        run, day = 0, last - timedelta(days=1)
        while (d := self.days.get(day.isoformat())) and d.costs:
            run, day = run + 1, day - timedelta(days=1)
        return run

    def result(self, names: dict[str, str], born: date | None = None) -> dict[str, Any]:
        today = date.today()
        month_from = today - timedelta(days=PERIOD_DAYS - 1)
        week_from = today - timedelta(days=6)
        active = sorted(k for k, d in self.days.items() if d.costs)
        first = date.fromisoformat(active[0]) if active else today
        first = min(first, born) if born else first
        # The heatmap: whole weeks back to the first activity, at least half a year.
        grid_from = (
            first if born else min(first, today - timedelta(weeks=HEATMAP_MIN_WEEKS))
        )
        grid_from -= timedelta(days=grid_from.weekday())

        def row(key: str) -> dict[str, Any]:
            d = self.days.get(key)
            return {
                "date": key,
                "cost": round(sum(d.costs.values()), 4) if d else 0.0,
                "out": d.out if d else 0,
                "turns": d.turns if d else 0,
            }

        def span(start: date) -> list[dict[str, Any]]:
            n = (today - start).days + 1
            return [row((start + timedelta(days=i)).isoformat()) for i in range(n)]

        week_cost: dict[str, float] = {}
        for i in range(7):
            d = self.days.get((week_from + timedelta(days=i)).isoformat())
            for sid, cost in d.costs.items() if d else ():
                week_cost[sid] = week_cost.get(sid, 0.0) + cost
        named = sorted(
            ((names[s], c) for s, c in week_cost.items() if s in names),
            key=lambda x: -x[1],
        )
        rest = sum(c for s, c in week_cost.items() if s not in names)
        rest += sum(c for _n, c in named[TOP_CHATS:])
        windows = {
            "today": self._window(today, today),
            "week": self._window(week_from, today),
            "month": self._window(month_from, today),
            "all": self._window(first, today),
        }
        windows["all"]["since"] = first.isoformat()
        return {
            "days": span(month_from),
            "heatmap": span(grid_from),
            "today": windows["today"],
            "week": windows["week"],
            "month": windows["month"],
            "all": windows["all"],
            "models": windows["month"]["models"],
            "chats": [{"name": n, "cost": round(c, 2)} for n, c in named[:TOP_CHATS]],
            "chats_rest": round(rest, 2),
            "born": born.isoformat() if born else None,
            "as_of": time.time(),
        }


def _openclaw_entries(file: Path) -> Iterator[dict[str, Any]]:
    """An OpenClaw session log, its assistant messages in transcript shape."""
    try:
        fh = file.open("rb")
    except OSError:
        return
    with fh:
        for raw in fh:
            if b'"usage"' not in raw:
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            msg = entry.get("message") if isinstance(entry, dict) else None
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict) or msg.get("provider") == "openclaw":
                continue  # "delivery-mirror": OpenClaw's own echo, not a model
            yield {
                "type": "assistant",
                "timestamp": entry.get("timestamp"),
                "message": {
                    "id": msg.get("responseId") or entry.get("id"),
                    "model": msg.get("model"),
                    "usage": {
                        "input_tokens": usage.get("input") or 0,
                        "output_tokens": usage.get("output") or 0,
                        "cache_read_input_tokens": usage.get("cacheRead") or 0,
                        "cache_creation_input_tokens": usage.get("cacheWrite") or 0,
                    },
                },
            }


def period_stats(
    private_root: Path,
    names: dict[str, str],
    *,
    born: date | None = None,
    history: Iterable[Path] = (),
) -> dict[str, Any]:
    """Everything the logs still hold: per day, windows, heatmap, chats.

    ``names`` maps a session id to its chat name; other sessions (SSH, cron
    jobs, headless helpers) are summed as one "other" row. ``history`` are
    folders of OpenClaw session logs (the agent's life before Claude Code;
    reset / deleted / checkpoint copies included, each reply counted once)
    and ``born`` starts the "all" window and the heatmap.
    """
    period = _Period()
    for project in _project_dirs(private_root):
        for file in project.glob("**/*.jsonl"):
            # subagents/agent-*.jsonl belong to the session dir above them
            sub = file.parent.name == "subagents"
            sid = file.parent.parent.name if sub else file.stem
            period.add_file(_entries(file), sid, not sub, set())
    seen: set[str] = set()  # checkpoints repeat their session's replies
    for folder in history:
        for file in sorted(folder.glob("*.jsonl*")):
            sid = "openclaw:" + file.name.split(".", 1)[0]
            period.add_file(_openclaw_entries(file), sid, True, seen)
    return period.result(names, born)


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
        born, history = _life()
        data = await asyncio.to_thread(
            lambda: period_stats(
                service._private_root(), names, born=born, history=history
            )
        )
        _period = (time.time(), data)
        return data


def _life() -> tuple[date | None, list[Path]]:
    """``[stats]`` in hub.toml: the agent's birthday and older session logs.

    ::

        [stats]
        born = "2026-02-25"
        history = ["~/.openclaw/agents/main/sessions"]
    """
    # Lazy: hub reads its config file on demand.
    from . import hub

    conf = hub.config().get("stats") or {}
    try:
        born = date.fromisoformat(str(conf["born"])) if conf.get("born") else None
    except ValueError:
        born = None
    history = [Path(os.path.expanduser(str(h))) for h in conf.get("history") or []]
    return born, [h for h in history if h.is_dir()]


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


async def part(name: str, thread_id: int | None) -> Any:
    """One block of the statistics sheet; each loads on its own."""
    # Lazy: usage pulls nothing heavy, but keep the pair side by side.
    from . import usage

    if name == "limits":
        return await usage.plan_usage()
    if name == "chat":
        return await _chat(thread_id) if thread_id is not None else None
    if name == "period":
        return await _period_cached()
    raise ValueError(name)


_warming: asyncio.Task[Any] | None = None


def warm() -> None:
    """Start the long pass in the background (the home page calls this), so
    the sheet usually opens on a warm cache."""
    global _warming
    stamp, data = _period
    fresh = data is not None and time.time() - stamp < PERIOD_TTL_S
    if fresh or (_warming is not None and not _warming.done()):
        return
    _warming = asyncio.get_running_loop().create_task(_period_cached())


def reset_for_testing() -> None:
    global _period
    _period = (0.0, None)
