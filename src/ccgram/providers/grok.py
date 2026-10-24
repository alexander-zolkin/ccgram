"""Grok Build provider — xAI's terminal agent behind the AgentProvider protocol.

Grok Build (the official xAI ``grok`` CLI, not the npm ``@vibe-kit/grok-cli``)
ships Claude-compatible lifecycle hooks, a structured JSONL transcript, and
``--resume`` / ``--continue`` recovery — so it maps cleanly onto ccgram's
existing provider machinery.

Message relay reads the whole ``chat_history.jsonl`` and diffs it by entry
index (``read_transcript_file``); the lines are interpreted by the shared
protocol parser (``session_protocol.messages``, using ``grok_format``'s rules);
idle/working transitions come from the installed hooks (SessionStart / Stop /
…).  Discovery under ``$GROK_HOME/sessions`` (see ``grok_discovery``) is the
fallback when a session is adopted without a hook firing first.

YOLO mode maps to ``--always-approve``.  Model selection is passed as
``--model <id>`` on a fresh launch (see the session-creation flow); mid-session
switching goes through the ``/model`` builtin.
"""

from __future__ import annotations

import json
import time
from typing import Any

from ccgram.providers._jsonl import JsonlProvider
from ccgram.providers.base import (
    RESUME_ID_RE,
    DiscoveredCommand,
    ProviderCapabilities,
    SessionStartEvent,
)
from ccgram.providers.grok_discovery import (
    GROK_TELEGRAM_BUILTINS,
    GROK_TUI_PICKERS,
    discover_grok_commands,
    discover_session_for_cwd,
)

# Cap transcript age when adopting a dead pane — guards against binding an
# unrelated historical session for the same cwd (mirrors pi/codex).
_STALE_TRANSCRIPT_MAX_AGE_SECS = 120.0


class GrokProvider(JsonlProvider):
    """AgentProvider implementation for the xAI Grok Build CLI."""

    _CAPS = ProviderCapabilities(
        name="grok",
        launch_command="grok",
        supports_hook=True,
        hook_install_managed_by_ccgram=True,
        supports_resume=True,
        supports_continue=True,
        supports_structured_transcript=True,
        # Grok rewrites chat_history.jsonl in place (not append-only), so a
        # stored byte offset can land mid-UTF-8-character on the next read and
        # crash the reader (UnicodeDecodeError). Read the whole file and diff by
        # entry index instead — see read_transcript_file.
        supports_incremental_read=False,
        builtin_commands=tuple(GROK_TELEGRAM_BUILTINS.keys()),
        supports_user_command_discovery=False,
        supports_status_snapshot=True,
        supports_model_picker=True,
        # Grok's home screen swallows keystrokes typed right after launch and it
        # only fires SessionStart once a prompt is submitted — so the topic's
        # first message is passed as a launch positional (``grok … "<prompt>"``).
        launch_accepts_initial_prompt=True,
        tui_picker_commands=GROK_TUI_PICKERS,
    )

    _BUILTINS = GROK_TELEGRAM_BUILTINS

    # ── Launch ───────────────────────────────────────────────────────────

    # ── Whole-file transcript read (grok rewrites chat_history.jsonl) ─────

    def read_transcript_file(
        self, file_path: str, last_offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Read the whole ``chat_history.jsonl`` and return entries past *last_offset*.

        ``last_offset`` is an entry index (count already processed), not a byte
        offset — grok rewrites the file, so byte offsets are unstable. Decoding
        uses ``errors="replace"`` so a transient partial write never crashes the
        poll loop. Returns ``(new_entries, total_entry_count)``.
        """
        entries: list[dict[str, Any]] = []
        try:
            with open(file_path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        parsed = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(parsed, dict):
                        entries.append(parsed)
        except OSError:
            return [], last_offset
        start = last_offset if isinstance(last_offset, int) and last_offset >= 0 else 0
        if start > len(entries):
            # File shrank / was reset — re-read from the top.
            start = 0
        return entries[start:], len(entries)

    def make_launch_args(
        self,
        resume_id: str | None = None,
        use_continue: bool = False,
    ) -> str:
        """Build Grok CLI args: ``--resume <id>`` / ``--continue``.

        The model override (``--model <id>``) is appended by the launch flow,
        not here, so resume/continue never force a model onto an existing
        session.
        """
        if resume_id:
            if not RESUME_ID_RE.match(resume_id):
                raise ValueError(f"Invalid resume_id: {resume_id!r}")
            return f"--resume {resume_id}"
        if use_continue:
            return "--continue"
        return ""

    # Transcript interpretation lives in ``session_protocol.messages``
    # (``_grok``), the one normalization path for live relay and history.

    # ── Discovery ────────────────────────────────────────────────────────

    def discover_transcript(
        self,
        cwd: str,
        window_key: str,
        *,
        max_age: float | None = None,
    ) -> SessionStartEvent | None:
        """Return the newest Grok session whose ``summary.json`` cwd matches."""
        if not cwd:
            return None
        age_limit = (
            _STALE_TRANSCRIPT_MAX_AGE_SECS if max_age is None else float(max_age)
        )
        found = discover_session_for_cwd(cwd, max_age=age_limit, now=time.time())
        if found is None:
            return None
        session_id, session_cwd, transcript_path = found
        return SessionStartEvent(
            session_id=session_id,
            cwd=session_cwd,
            transcript_path=str(transcript_path),
            window_key=window_key,
        )

    # ── Commands ─────────────────────────────────────────────────────────

    def discover_commands(self, base_dir: str) -> list[DiscoveredCommand]:
        return discover_grok_commands(base_dir)

    # ── Status snapshot ──────────────────────────────────────────────────

    def build_status_snapshot(
        self,
        transcript_path: str,
        *,
        display_name: str = "",
        session_id: str = "",
        cwd: str = "",
    ) -> str | None:
        # Lazy: grok_status pulls transcript parsers; defer until a polling tick.
        from ccgram.providers.grok_status import build_grok_status_snapshot

        return build_grok_status_snapshot(
            transcript_path,
            display_name=display_name,
            session_id=session_id,
            cwd=cwd,
        )

    def has_output_since(self, transcript_path: str, offset: int) -> bool:
        # Lazy: grok_status pulls transcript parsers; defer until a polling tick.
        from ccgram.providers.grok_status import has_grok_assistant_output_since

        return has_grok_assistant_output_since(transcript_path, offset)
