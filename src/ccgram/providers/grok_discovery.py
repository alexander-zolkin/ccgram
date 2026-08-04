"""Grok Build session discovery and Telegram command catalog.

Grok stores each session under::

    $GROK_HOME/sessions/<encoded-cwd>/<session-uuid>/
        summary.json         # metadata: info.id, info.cwd, current_model_id, …
        chat_history.jsonl   # raw chat messages (ccgram's relay source)
        events.jsonl         # turn/phase lifecycle events
        signals.json         # token / tool counters

``<encoded-cwd>`` is the URL-percent-encoding of the absolute working directory
(``/root`` → ``%2Froot``).  When the encoded name would exceed 255 bytes Grok
falls back to a slug+hash directory and records the real path in a ``.cwd``
file, so discovery scans every group directory and matches ``summary.json``'s
``info.cwd`` rather than trusting the encoded name alone.

``GROK_HOME`` overrides the base directory; when unset Grok uses ``~/.grok``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote

from ccgram.providers.base import DiscoveredCommand

# Newest N session dirs to inspect when matching a cwd — keeps discovery O(1)
# on machines with a long session history.
_DISCOVERY_SCAN_LIMIT = 40

# The single ``chat_history.jsonl`` file ccgram reads incrementally for relay.
CHAT_HISTORY_NAME = "chat_history.jsonl"


def grok_home() -> Path:
    """Resolve the Grok home directory (``$GROK_HOME`` or ``~/.grok``)."""
    override = os.environ.get("GROK_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".grok"


def grok_sessions_dir() -> Path:
    """Return the Grok sessions root under the resolved home directory."""
    return grok_home() / "sessions"


def encode_cwd_dirname(cwd: str) -> str:
    """Encode a working directory into Grok's session group name.

    Mirrors Grok's URL-percent-encoding: every character outside the unreserved
    set (``A-Z a-z 0-9 - . _ ~``) is percent-encoded, so ``/root`` becomes
    ``%2Froot`` and ``/home/x`` becomes ``%2Fhome%2Fx``.  Trailing separators are
    dropped first so ``/a/b`` and ``/a/b/`` collide correctly.
    """
    trimmed = cwd.rstrip("/\\") or "/"
    return quote(trimmed, safe="")


def transcript_path_for(cwd: str, session_id: str) -> Path:
    """Build the ``chat_history.jsonl`` path for a known cwd + session id."""
    return (
        grok_sessions_dir() / encode_cwd_dirname(cwd) / session_id / CHAT_HISTORY_NAME
    )


def read_summary(session_dir: Path) -> dict | None:
    """Read and parse a session's ``summary.json`` (None on any failure)."""
    try:
        raw = (session_dir / "summary.json").read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def summary_cwd(summary: dict) -> str:
    """Extract ``info.cwd`` from a parsed summary (empty string if absent)."""
    info = summary.get("info")
    if isinstance(info, dict):
        cwd = info.get("cwd")
        if isinstance(cwd, str):
            return cwd
    return ""


def summary_session_id(summary: dict) -> str:
    """Extract ``info.id`` from a parsed summary (empty string if absent)."""
    info = summary.get("info")
    if isinstance(info, dict):
        sid = info.get("id")
        if isinstance(sid, str):
            return sid
    return ""


def iter_session_dirs() -> list[tuple[float, Path]]:
    """Return ``(mtime, session_dir)`` for every session, newest-first.

    A session directory is any leaf that contains a ``summary.json``; the
    two-level layout is ``<group>/<session-uuid>/`` but we glob defensively so
    an unexpected nesting depth still yields the right leaves.
    """
    root = grok_sessions_dir()
    if not root.is_dir():
        return []
    results: list[tuple[float, Path]] = []
    try:
        group_dirs = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return []
    for group in group_dirs:
        try:
            session_dirs = [d for d in group.iterdir() if d.is_dir()]
        except OSError:
            continue
        for sdir in session_dirs:
            summary = sdir / "summary.json"
            try:
                mtime = summary.stat().st_mtime
            except OSError:
                continue
            results.append((mtime, sdir))
    results.sort(key=lambda pair: pair[0], reverse=True)
    return results


def _group_candidates(group: Path) -> list[tuple[float, Path]]:
    """Return ``(mtime, session_dir)`` under one group dir, newest-first."""
    out: list[tuple[float, Path]] = []
    try:
        for sdir in group.iterdir():
            if not sdir.is_dir():
                continue
            try:
                mtime = (sdir / "summary.json").stat().st_mtime
            except OSError:
                continue
            out.append((mtime, sdir))
    except OSError:
        return []
    out.sort(key=lambda pair: pair[0], reverse=True)
    return out


def _session_matches(sdir: Path, target: str) -> tuple[str, str] | None:
    """Return ``(session_id, session_cwd)`` when *sdir*'s summary cwd == target."""
    summary = read_summary(sdir)
    if summary is None:
        return None
    session_cwd = summary_cwd(summary)
    if not session_cwd:
        return None
    try:
        if str(Path(session_cwd).resolve()) != target:
            return None
    except OSError:
        return None
    return summary_session_id(summary) or sdir.name, session_cwd


def discover_session_for_cwd(
    cwd: str, *, max_age: float | None = None, now: float | None = None
) -> tuple[str, str, Path] | None:
    """Find the newest Grok session whose ``summary.json`` cwd matches *cwd*.

    Returns ``(session_id, session_cwd, chat_history_path)`` or None.  The fast
    path checks the encoded-cwd group directly; the fallback scans recent
    sessions and compares resolved cwds (covering the slug+hash long-path case).
    """
    if not cwd:
        return None
    try:
        target = str(Path(cwd).resolve())
    except OSError:
        return None

    # Lazy: time is only needed for the optional freshness (max_age) check.
    import time

    current = time.time() if now is None else now

    # Fast path: the encoded group directory for this exact cwd; fall back to a
    # broad scan of the newest sessions across all groups.
    fast_group = grok_sessions_dir() / encode_cwd_dirname(cwd)
    candidates = _group_candidates(fast_group) if fast_group.is_dir() else []
    if not candidates:
        candidates = iter_session_dirs()[:_DISCOVERY_SCAN_LIMIT]

    stale = max_age is not None and max_age > 0
    for mtime, sdir in candidates:
        if stale and (current - mtime) > max_age:  # type: ignore[operator]
            continue
        match = _session_matches(sdir, target)
        if match is not None:
            return match[0], match[1], sdir / CHAT_HISTORY_NAME
    return None


# ── Telegram-exposed slash commands ───────────────────────────────────────

# Grok Build built-in slash commands that are safe to surface over Telegram.
# Sourced from ~/.grok/docs/user-guide/04-slash-commands.md.  /resume is
# excluded on purpose — it collides with ccgram's own session picker.  Modal
# pickers (see GROK_TUI_PICKERS) are still listed here so autocomplete shows
# them; the inline toolbar drives their in-TUI navigation.
GROK_TELEGRAM_BUILTINS: dict[str, str] = {
    "/model": "Switch models",
    "/effort": "Set reasoning effort (high/medium/low)",
    "/compact": "Compact conversation context",
    "/context": "Show context window usage",
    "/session-info": "Show session id, model, and token usage",
    "/new": "Start a fresh session (clears context)",
    "/clear": "Alias for /new",
    "/plan": "Enter plan mode",
    "/view-plan": "Show the current plan",
    "/rewind": "Restore files to an earlier point",
    "/fork": "Fork the session from an earlier turn",
    "/rename": "Rename the session title",
    "/copy": "Copy the last response",
    "/export": "Export the session transcript as Markdown",
    "/usage": "Show token usage and cost",
    "/memory": "Manage cross-session memory",
    "/mcps": "List configured MCP servers",
    "/skills": "List available skills",
    "/history": "Browse conversation history",
    "/theme": "Change the color theme",
    "/settings": "Open Grok settings",
    "/vim-mode": "Toggle vim editing mode",
    "/quit": "Quit Grok",
}

# Subset of GROK_TELEGRAM_BUILTINS (bare names) that open a modal in-TUI picker
# the user must drive with arrow keys / Enter / Esc.  Kept lowercase and free of
# the leading slash so forward.py's cc_name lookup matches (see
# test_picker_capability_drift).
GROK_TUI_PICKERS: frozenset[str] = frozenset(
    {
        "model",
        "rewind",
        "fork",
        "skills",
        "history",
        "theme",
        "settings",
        "memory",
    }
)


def discover_grok_commands(base_dir: str) -> list[DiscoveredCommand]:  # noqa: ARG001
    """Return the Telegram-safe Grok built-in commands.

    ``base_dir`` is accepted for protocol parity; Grok's user-defined skills are
    surfaced mid-session via ``/skills`` rather than enumerated here.
    """
    return [
        DiscoveredCommand(name=name, description=desc, source="builtin")
        for name, desc in GROK_TELEGRAM_BUILTINS.items()
    ]
