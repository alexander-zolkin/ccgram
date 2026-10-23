"""What the Claude Code sessions would have cost at API prices.

On a subscription nothing is billed per token; this is the value of the work —
the same figure Claude Code's /usage shows as a session's "Total cost". Every
API message's usage is in the transcript (repeated on each content block, so
counted once per message id); prices are per million tokens. A chat's numbers
include its subagents (``<session>/subagents/*.jsonl``). The 30-day overview
walks every project's transcripts except the private root's — a full pass
takes a couple of seconds, so it is cached for a few minutes.

"Everything" adds the rest of the work at each provider's list prices:
Codex CLI sessions (ChatGPT plan), the OpenClaw era's GPT / Gemini / Grok /
Qwen replies, and other machines' Claude Code and Codex transcripts.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

# $ per million tokens: input, output, cache read, cache write 5 min, 1 h.
# Other providers bill no cache writes, and their long-context tiers (prompts
# over 200k / 272k) are not applied. Their list prices as of October 2026.
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
    # OpenAI: Codex on the ChatGPT plan, and OpenClaw
    "gpt-6-luna": (0.1, 0.5, 0.01, 0.1, 0.1),
    "gpt-5.6-terra": (2.0, 12.0, 0.2, 2.0, 2.0),
    "gpt-5.5": (5.0, 30.0, 0.5, 5.0, 5.0),
    "gpt-5.4-mini": (0.75, 4.5, 0.075, 0.75, 0.75),
    "gpt-5.4": (2.5, 15.0, 0.25, 2.5, 2.5),
    "gpt-5.3-codex": (1.75, 14.0, 0.175, 1.75, 1.75),
    "gpt-5.2-codex": (1.75, 14.0, 0.175, 1.75, 1.75),
    "gpt-5.1-codex-mini": (0.25, 2.0, 0.025, 0.25, 0.25),
    # xAI, Google, Alibaba: OpenClaw ("coder-model" is Qwen3 Coder Plus)
    "grok-4.20": (1.25, 2.5, 0.2, 1.25, 1.25),
    "grok-4-1-fast": (0.2, 0.5, 0.05, 0.2, 0.2),
    "gemini-3.1-pro": (2.0, 12.0, 0.2, 2.0, 2.0),
    "gemini-3-flash": (0.5, 3.0, 0.05, 0.5, 0.5),
    "gemini-2.5-pro": (1.25, 10.0, 0.125, 1.25, 1.25),
    "coder-model": (1.0, 5.0, 0.2, 1.0, 1.0),
}
FREE = (0.0, 0.0, 0.0, 0.0, 0.0)
# Who makes a model, by its name: the "everything" block groups by provider.
VENDORS = (
    ("claude-", "anthropic"),
    ("gpt-", "openai"),
    ("codex-", "openai"),
    ("grok-", "xai"),
    ("gemini-", "google"),
    ("gemma-", "google"),
    ("qwen", "alibaba"),
    ("coder-model", "alibaba"),
    ("llama-", "meta"),
)
FAST_FACTOR = 2.0  # fast mode bills double
PERIOD_DAYS = 30
PERIOD_TTL_S = 30.0  # past days come from the store; only today is re-read
CHUNK = 256 * 1024
STORE_VERSION = 1
TOP_CHATS = 6
HEATMAP_MIN_WEEKS = 26


def price(model: str) -> tuple[float, float, float, float, float] | None:
    base = model.split("[", 1)[0]
    if base.endswith(":free"):  # OpenRouter's free tier
        return FREE
    base = base.rsplit("/", 1)[-1]  # "vendor/model" through a router
    best = max((k for k in PRICES if base.startswith(k)), key=len, default=None)
    return PRICES[best] if best else None


def provider(model: str) -> str:
    name = model.split("[", 1)[0].rsplit("/", 1)[-1].lower()
    return next((who for head, who in VENDORS if name.startswith(head)), "other")


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
        return self.add_parts(
            model, _usage_parts(usage), fast=usage.get("speed") == "fast"
        )

    def add_parts(
        self, model: str, parts: tuple[int, int, int, int, int], fast: bool = False
    ) -> float:
        rates = price(model)
        cost = 0.0
        if rates:
            cost = sum(n * r for n, r in zip(parts, rates, strict=True)) / 1e6
            if fast:
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


_WANTED = (b'"usage"', b"turn_duration", b"structuredPatch")


def _parse(raw: bytes) -> dict[str, Any] | None:
    """A transcript line worth parsing (usage, turn end, code change)."""
    if not any(key in raw for key in _WANTED):
        return None
    try:
        entry = json.loads(raw)
    except ValueError:
        return None
    return entry if isinstance(entry, dict) else None


def _entries(path: Path) -> Iterator[dict[str, Any]]:
    try:
        fh = path.open("rb")
    except OSError:
        return
    with fh:
        for raw in fh:
            if (entry := _parse(raw)) is not None:
                yield entry


def _tail_entries(path: Path, since: float) -> Iterator[dict[str, Any]]:
    """Entries stamped at or after ``since``, newest first. Transcripts only
    grow, so reading backwards stops at the first older entry."""
    try:
        fh = path.open("rb")
    except OSError:
        return
    with fh:
        pos, carry = fh.seek(0, os.SEEK_END), b""
        while pos > 0:
            start = max(0, pos - CHUNK)
            fh.seek(start)
            lines = (fh.read(pos - start) + carry).split(b"\n")
            pos = start
            carry = lines.pop(0) if start > 0 else b""
            for raw in reversed(lines):
                if (entry := _parse(raw)) is None:
                    continue
                when = _epoch(entry.get("timestamp"))
                if when is not None and when < since:
                    return
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


class _ChatFile:
    """One transcript as far as it has been read; later calls read only what
    was appended since."""

    def __init__(self) -> None:
        self.offset = 0
        self.tally, self.seen = Tally(), set()
        self.turns = self.busy_ms = self.added = self.removed = 0
        self.first: float | None = None
        self.last: float | None = None

    def feed(self, path: Path, main: bool) -> None:
        try:
            size = path.stat().st_size
            if size < self.offset:  # rewritten: start over
                self.__init__()
            with path.open("rb") as fh:
                fh.seek(self.offset)
                data = fh.read(size - self.offset)
        except OSError:
            return
        complete = data[: data.rfind(b"\n") + 1]  # a line being written waits
        self.offset += len(complete)
        for raw in complete.splitlines():
            if (entry := _parse(raw)) is not None:
                self._take(entry, main)

    def _take(self, entry: dict[str, Any], main: bool) -> None:
        when = _epoch(entry.get("timestamp"))
        if main and when is not None:  # subagents add cost, not turns or time
            self.first = when if self.first is None else min(self.first, when)
            self.last = when if self.last is None else max(self.last, when)
        if _is_turn_end(entry):
            if main:
                self.turns += 1
                self.busy_ms += int(entry.get("durationMs") or 0)
            return
        plus, minus = _patch_lines(entry.get("toolUseResult"))
        self.added, self.removed = self.added + plus, self.removed + minus
        priced = _priced_message(entry)
        if priced is not None and priced[0] not in self.seen:
            self.seen.add(priced[0])
            self.tally.add(priced[1], priced[2])


_chat_files: dict[str, _ChatFile] = {}


def chat_stats(path: Path) -> dict[str, Any]:
    """One chat: cost and tokens by model, turns, time, code changed."""
    files = [_chat_files.setdefault(str(f), _ChatFile()) for f in _files_of(path)]
    tally = Tally()
    for i, (file, state) in enumerate(zip(_files_of(path), files, strict=True)):
        state.feed(file, i == 0)
        tally.merge(state.tally)
    main = files[0]
    return {
        **tally.summary(),
        "turns": main.turns,
        "busy_s": round(main.busy_ms / 1000),
        "wall_s": round(main.last - main.first) if main.first and main.last else 0,
        "lines_added": sum(f.added for f in files),
        "lines_removed": sum(f.removed for f in files),
        "subagents": len(files) - 1,
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
        self.seen: set[str] = set()  # reply ids counted (archives repeat them)

    def dump(self, until: date) -> dict[str, Any]:
        """Days before ``until`` — finished, they never change again."""
        cut = until.isoformat()
        return {
            "days": {
                key: {"m": day.tally.models, "t": day.turns, "c": day.costs}
                for key, day in self.days.items()
                if key < cut
            },
            "spans": self.spans,
        }

    @classmethod
    def load(cls, data: dict[str, Any]) -> _Period:
        period = cls()
        for key, raw in (data.get("days") or {}).items():
            day = period.days[key] = _Day()
            day.tally.models = {m: list(r) for m, r in (raw.get("m") or {}).items()}
            day.turns, day.costs = int(raw.get("t") or 0), dict(raw.get("c") or {})
        period.spans = {s: list(v) for s, v in (data.get("spans") or {}).items()}
        return period

    def absorb(self, newer: _Period) -> None:
        """Days tallied since the store was closed, and their sessions' spans."""
        self.days.update(newer.days)
        for sid, (first, last) in newer.spans.items():
            span = self.spans.setdefault(sid, [first, last])
            span[0], span[1] = min(span[0], first), max(span[1], last)

    def copy(self) -> _Period:
        twin = _Period()
        for key, day in self.days.items():
            mine = twin.days[key] = _Day()
            mine.tally.merge(day.tally)
            mine.turns, mine.costs = day.turns, dict(day.costs)
        twin.spans = {sid: list(span) for sid, span in self.spans.items()}
        twin.seen = set(self.seen)
        return twin

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


def _claude_files(root: Path, hidden: str) -> Iterator[tuple[Path, str, bool]]:
    """(transcript, session id, is the main transcript) under a projects dir."""
    for file in root.glob("**/*.jsonl"):
        if any(part.startswith(hidden) for part in file.parts):
            continue  # the private root's project dirs
        # subagents/agent-*.jsonl belong to the session dir above them
        sub = file.parent.name == "subagents"
        yield file, file.parent.parent.name if sub else file.stem, not sub


def _hidden(private_root: Path) -> str:
    return "-" + str(private_root).strip("/").replace("/", "-").replace(".", "-")


_archived: tuple[tuple[Any, ...], _Period] | None = None


def _archive_period(
    private_root: Path, archive: Iterable[Path], history: Iterable[Path]
) -> _Period:
    """Old Claude Code transcripts and OpenClaw logs: they no longer change, so
    they are tallied once and kept until a file's size or mtime moves."""
    global _archived
    hidden = _hidden(private_root)
    claude = [f for root in archive for f in _claude_files(root, hidden)]
    openclaw = sorted(f for folder in history for f in folder.glob("**/*.jsonl*"))
    sig = tuple(
        (str(f), st.st_size, st.st_mtime)
        for f in [c[0] for c in claude] + openclaw
        if (st := f.stat())
    )
    if _archived is not None and _archived[0] == sig:
        return _archived[1]
    period = _Period()
    for file, sid, main in claude:
        period.add_file(_entries(file), sid, main, period.seen)
    replies: set[str] = set()  # OpenClaw ids live apart: checkpoints repeat them
    for file in openclaw:
        sid = "openclaw:" + file.name.split(".", 1)[0]
        period.add_file(_openclaw_entries(file), sid, True, replies)
    _archived = (sig, period)
    return period


def _signature(
    private_root: Path, born: date | None, history: list[Path], archive: list[Path]
) -> str:
    """What the stored days were computed from: the sources, the prices and
    every archived file's size and mtime — any change means a rebuild."""
    hidden = _hidden(private_root)
    files = [f for root in archive for f, _s, _m in _claude_files(root, hidden)]
    files += [f for folder in history for f in folder.glob("**/*.jsonl*")]
    stats = sorted((str(f), f.stat().st_size, f.stat().st_mtime) for f in files)
    basis = [STORE_VERSION, sorted(PRICES.items()), str(born), hidden, stats]
    return hashlib.sha1(json.dumps(basis, default=str).encode()).hexdigest()


def _load_store(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    return data if isinstance(data, dict) else None


def _save_store(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def period_stats(
    private_root: Path,
    names: dict[str, str],
    *,
    born: date | None = None,
    history: Iterable[Path] = (),
    archive: Iterable[Path] = (),
    store: Path | None = None,
) -> dict[str, Any]:
    """Everything the logs still hold: per day, windows, heatmap, chats.

    ``names`` maps a session id to its chat name; other sessions (SSH, cron
    jobs, headless helpers) are summed as one "other" row. ``archive`` are
    older copies of Claude Code projects dirs (backups of transcripts Claude
    Code has since cleaned up) and ``history`` folders of OpenClaw session
    logs — the agent's life before Claude Code, reset / deleted / checkpoint
    copies included, each reply counted once; ``born`` starts the "all"
    window and the heatmap.

    With a ``store``, finished days are kept on disk: a later call re-reads
    only the transcripts touched since the last stored day, from their tails
    — and the history survives even if the transcripts are cleaned up.
    """
    period = _build_period(
        private_root, born=born, history=history, archive=archive, store=store
    )
    return period.result(names, born)


def _build_period(
    private_root: Path,
    *,
    born: date | None,
    history: Iterable[Path],
    archive: Iterable[Path],
    store: Path | None,
) -> _Period:
    history, archive = list(history), list(archive)
    today = date.today()
    sig = _signature(private_root, born, history, archive) if store else ""
    saved = _load_store(store) if store else None
    if saved and saved.get("sig") == sig and saved.get("closed_until"):
        period = _Period.load(saved)
        opened = date.fromisoformat(saved["closed_until"])
        since = datetime.combine(opened, datetime.min.time()).timestamp()
        newer = _Period()
        for project in _project_dirs(private_root):
            for file, sid, main in _claude_files(project, _hidden(private_root)):
                with contextlib.suppress(OSError):
                    if file.stat().st_mtime >= since:
                        newer.add_file(
                            _tail_entries(file, since), sid, main, newer.seen
                        )
        period.absorb(newer)
    else:
        period = _archive_period(private_root, archive, history).copy()
        for project in _project_dirs(private_root):
            for file, sid, main in _claude_files(project, _hidden(private_root)):
                period.add_file(_entries(file), sid, main, period.seen)
    if store:
        _save_store(
            store, {**period.dump(today), "sig": sig, "closed_until": today.isoformat()}
        )
    return period


# ── everything: every machine, every model ───────────────────────────────────

# (dedup key, local day, model, usage parts, fast mode) — one model request.
type _Row = tuple[str, str, str, tuple[int, int, int, int, int], bool]
_CODEX_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens")


def _local_day(when: float) -> str:
    return datetime.fromtimestamp(when).date().isoformat()


def _claude_rows(file: Path) -> list[_Row]:
    rows: list[_Row] = []
    for entry in _entries(file):
        priced, when = _priced_message(entry), _epoch(entry.get("timestamp"))
        if priced is not None and when is not None:
            mid, model, usage = priced
            fast = usage.get("speed") == "fast"
            rows.append((mid, _local_day(when), model, _usage_parts(usage), fast))
    return rows


def _codex_events(file: Path) -> Iterator[tuple[str, dict[str, Any], Any]]:
    """(entry type, payload, timestamp) of the rollout lines that matter."""
    try:
        fh = file.open("rb")
    except OSError:
        return
    with fh:
        for raw in fh:
            if not any(k in raw for k in (b"token_count", b"turn_context", b"_meta")):
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            payload = entry.get("payload") if isinstance(entry, dict) else None
            if isinstance(payload, dict):
                yield str(entry.get("type")), payload, entry.get("timestamp")


def _codex_rows(file: Path, private: str) -> list[_Row]:
    """A Codex CLI rollout, one row per model request. ``token_count`` events
    carry the session's running total, so a request is the step from the
    previous one; the first event, or one after the total went back, counts
    its ``last_token_usage``. OpenAI counts cached input inside input. A
    session started under the private root counts nothing."""
    rows: list[_Row] = []
    model, prev = "", None
    for kind, payload, stamp in _codex_events(file):
        if kind == "session_meta" and str(payload.get("cwd")).startswith(private):
            return []
        if kind == "turn_context":
            model = str(payload.get("model") or model)
        info = payload.get("info") if payload.get("type") == "token_count" else None
        if not isinstance(info, dict):
            continue
        total = info.get("total_token_usage") or {}
        now = tuple(int(total.get(k) or 0) for k in _CODEX_KEYS)
        if prev is not None and all(n >= p for n, p in zip(now, prev, strict=True)):
            used = tuple(n - p for n, p in zip(now, prev, strict=True))
        else:
            last = info.get("last_token_usage") or {}
            used = tuple(int(last.get(k) or 0) for k in _CODEX_KEYS)
        prev = now
        when = _epoch(stamp)
        if when is not None and any(used):
            fed, cached, out = used
            parts = (max(0, fed - cached), out, cached, 0, 0)
            rows.append((f"{stamp}/{sum(now)}", _local_day(when), model, parts, False))
    return rows


def _codex_files(dirs: Iterable[Path]) -> list[Path]:
    """Rollouts under the dirs; a session kept in two places (the live dir
    and its backup) counts once, from the longer copy."""
    best: dict[str, tuple[int, Path]] = {}
    for folder in dirs:
        for file in folder.glob("**/rollout-*.jsonl"):
            with contextlib.suppress(OSError):
                size = file.stat().st_size
                if size > best.get(file.name, (-1, file))[0]:
                    best[file.name] = (size, file)
    return sorted(path for _size, path in best.values())


_rows: dict[str, tuple[int, float, list[_Row]]] = {}


def _rows_of(file: Path, read: Callable[[Path], list[_Row]]) -> list[_Row]:
    """A file's rows, read again only once its size or mtime moved."""
    try:
        st = file.stat()
    except OSError:
        return []
    known = _rows.get(str(file))
    if known and known[:2] == (st.st_size, st.st_mtime):
        return known[2]
    rows = read(file)
    _rows[str(file)] = (st.st_size, st.st_mtime, rows)
    return rows


class _Spend:
    """Dollars and tokens by month and model, dollars by machine and tool."""

    def __init__(self) -> None:
        self.months: dict[str, Tally] = {}
        self.where: dict[str, dict[str, float]] = {}  # machine → tool → $
        self.first: str | None = None

    def _book(self, day: str, machine: str, tool: str, cost: float) -> None:
        tools = self.where.setdefault(machine, {})
        tools[tool] = tools.get(tool, 0.0) + cost
        self.first = day if self.first is None else min(self.first, day)

    def add(self, row: _Row, machine: str, tool: str) -> None:
        _key, day, model, parts, fast = row
        month = self.months.setdefault(day[:7], Tally())
        self._book(day, machine, tool, month.add_parts(model, parts, fast))

    def absorb(self, period: _Period, machine: str) -> None:
        """This machine's days: Claude Code and the OpenClaw era."""
        for key, day in period.days.items():
            if not day.costs:
                continue
            self.months.setdefault(key[:7], Tally()).merge(day.tally)
            openclaw = sum(c for s, c in day.costs.items() if s.startswith("openclaw:"))
            self._book(key, machine, "OpenClaw", openclaw)
            self._book(key, machine, "Claude Code", sum(day.costs.values()) - openclaw)

    def result(self) -> dict[str, Any]:
        total = Tally()
        months = []
        for key in sorted(self.months):
            tally = self.months[key]
            total.merge(tally)
            by: dict[str, float] = {}
            for model, row in tally.models.items():
                by[provider(model)] = by.get(provider(model), 0.0) + row[4]
            months.append(
                {
                    "month": key,
                    "cost": round(sum(by.values()), 2),
                    "providers": {p: round(c, 2) for p, c in by.items() if c},
                }
            )
        summary = total.summary()
        used = ("input", "output", "cache_read", "cache_write")
        models = [
            {**m, "provider": provider(m["model"])}
            for m in summary["models"]
            if any(m[k] for k in used)  # OpenClaw tried some models in vain
        ]
        vendors: dict[str, list[float]] = {}
        for m in models:
            row = vendors.setdefault(m["provider"], [0.0, 0.0])
            row[0] += m["cost"]
            row[1] += sum(m[k] for k in used)
        machines = [
            {
                "name": name,
                "cost": round(sum(tools.values()), 2),
                "tools": [
                    {"name": t, "cost": round(c, 2)}
                    for t, c in sorted(tools.items(), key=lambda kv: -kv[1])
                    if round(c, 2)
                ],
            }
            for name, tools in self.where.items()
        ]
        return {
            "cost": round(summary["cost"], 2),
            "tokens": sum(int(r[1]) for r in vendors.values()),
            "since": self.first,
            "providers": [
                {"provider": p, "cost": round(r[0], 2), "tokens": int(r[1])}
                for p, r in sorted(
                    vendors.items(), key=lambda kv: (-kv[1][0], -kv[1][1])
                )
            ],
            "machines": sorted(machines, key=lambda m: -m["cost"]),
            "months": months,
            "models": models,
            "as_of": time.time(),
        }


type Machine = tuple[str, list[Path], list[Path]]  # name, Claude dirs, Codex dirs


def spend(
    period: _Period,
    private_root: Path,
    *,
    here: str,
    codex: Iterable[Path] = (),
    machines: Iterable[Machine] = (),
) -> dict[str, Any]:
    """Everything at API prices: this machine's period (Claude Code and the
    OpenClaw era), its Codex sessions, and other machines' Claude Code and
    Codex transcripts (copies pushed to this one). Each file is parsed once
    until it changes; a request repeated in two files (a forked session)
    counts once."""
    out = _Spend()
    out.absorb(period, here)
    hidden, private = _hidden(private_root), str(private_root)
    seen: set[str] = set()

    def take(rows: list[_Row], machine: str, tool: str) -> None:
        for row in rows:
            if row[0] not in seen:
                seen.add(row[0])
                out.add(row, machine, tool)

    for name, claude, codex_dirs in [(here, [], list(codex)), *machines]:
        for root in claude:
            for file, _sid, _main in _claude_files(root, hidden):
                take(_rows_of(file, _claude_rows), name, "Claude Code")
        for file in _codex_files(codex_dirs):
            take(_rows_of(file, lambda f: _codex_rows(f, private)), name, "Codex")
    return out.result()


# ── what the statistics sheet asks for ───────────────────────────────────────

_period: tuple[float, dict[str, Any] | None, _Period | None] = (0.0, None, None)
_period_lock = asyncio.Lock()
_total: tuple[float, dict[str, Any] | None] = (0.0, None)
_total_lock = asyncio.Lock()


async def _period_cached() -> dict[str, Any] | None:
    global _period
    # Lazy: service pulls the handler graph.
    from . import service

    async with _period_lock:
        stamp, data, _obj = _period
        if data is not None and time.time() - stamp < PERIOD_TTL_S:
            return data
        names: dict[str, str] = {}
        for s in await service.list_sessions():  # private chats are not listed
            with contextlib.suppress(service.WebUIError):
                tx, _ = service._transcript_for(service.find_binding(s["thread_id"]))
                if tx:
                    names[tx.stem] = s["name"]
        born, history, archive = _life()

        def build() -> tuple[dict[str, Any], _Period]:
            period = _build_period(
                service._private_root(),
                born=born,
                history=history,
                archive=archive,
                store=_store_path(),
            )
            return period.result(names, born), period

        data, period = await asyncio.to_thread(build)
        _period = (time.time(), data, period)
        return data


async def _total_cached() -> dict[str, Any] | None:
    global _total
    # Lazy: service pulls the handler graph.
    from . import service

    async with _total_lock:
        stamp, data = _total
        if data is not None and time.time() - stamp < PERIOD_TTL_S:
            return data
        await _period_cached()
        period = _period[2]
        if period is None:
            return None
        here, codex, machines = _elsewhere()
        data = await asyncio.to_thread(
            lambda: spend(
                period,
                service._private_root(),
                here=here,
                codex=codex,
                machines=machines,
            )
        )
        _total = (time.time(), data)
        return data


def _store_path() -> Path:
    base = Path(os.getenv("CCGRAM_DIR") or os.path.expanduser("~/.ccgram"))
    return base / "stats_days.json"


def _life() -> tuple[date | None, list[Path], list[Path]]:
    """``[stats]`` in hub.toml: the agent's birthday and older session logs.

    ::

        [stats]
        born = "2026-02-25"
        history = ["~/.openclaw/agents/main/sessions"]   # OpenClaw logs
        archive = ["/backups/claude-projects"]           # old transcripts
    """
    # Lazy: hub reads its config file on demand.
    from . import hub

    conf = hub.config().get("stats") or {}
    try:
        born = date.fromisoformat(str(conf["born"])) if conf.get("born") else None
    except ValueError:
        born = None
    return born, _dirs(conf, "history"), _dirs(conf, "archive")


def _dirs(conf: dict[str, Any], key: str) -> list[Path]:
    paths = [Path(os.path.expanduser(str(h))) for h in conf.get(key) or []]
    return [h for h in paths if h.is_dir()]


def _elsewhere() -> tuple[str, list[Path], list[Machine]]:
    """``[stats]`` in hub.toml, the rest of the work for "everything": this
    machine's name and Codex sessions, and other machines' transcripts.

    ::

        [stats]
        here = "Сервер"
        codex = ["~/.codex/sessions"]                    # Codex rollouts
        [[stats.machines]]
        name = "Ноутбук"
        claude = ["/backups/laptop/claude-projects"]     # Claude Code projects
        codex = ["/backups/laptop/codex-sessions"]
    """
    # Lazy: hub reads its config file on demand.
    from . import hub

    conf = hub.config().get("stats") or {}
    machines: list[Machine] = [
        (str(m.get("name") or "?"), _dirs(m, "claude"), _dirs(m, "codex"))
        for m in conf.get("machines") or []
        if isinstance(m, dict)
    ]
    return str(conf.get("here") or "Здесь"), _dirs(conf, "codex"), machines


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
    if name == "limits-known":  # instant: the last answer, no request
        return await usage.known_usage()
    if name == "chat":
        return await _chat(thread_id) if thread_id is not None else None
    if name == "period":
        return await _period_cached()
    if name == "total":
        return await _total_cached()
    raise ValueError(name)


_warming: asyncio.Task[Any] | None = None


def warm() -> None:
    """Start the long pass in the background (the home page calls this), so
    the sheet usually opens on a warm cache."""
    global _warming
    stamp, data = _total
    fresh = data is not None and time.time() - stamp < PERIOD_TTL_S
    if fresh or (_warming is not None and not _warming.done()):
        return
    _warming = asyncio.get_running_loop().create_task(_total_cached())


def reset_for_testing() -> None:
    global _period, _total, _archived
    _period = (0.0, None, None)
    _total = (0.0, None)
    _archived = None
    _chat_files.clear()
    _rows.clear()
