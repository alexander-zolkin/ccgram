"""One-shot suppression of the harness ``--continue`` placeholder round.

CCGRAM-HOTFIX:skip-synthetic-continue

When ccgram autoresumes a hibernated session it launches ``claude --continue``,
which makes the Claude Code harness run a stock "Continue from where you left
off." turn — a placeholder *prompt* plus the model's no-op *reply* — before the
real forwarded message is processed.

The placeholder prompt itself is dropped globally in ``message_routing`` (it is
never something the user typed). This module additionally lets the autoresume
path *arm* a window so the model's reply to that placeholder is suppressed too.
The legitimate ``/continue`` command never arms a window, so its reply is always
shown. A real forwarded user turn (or any tool call) disarms the window, so a
genuine reply is never hidden.
"""

from __future__ import annotations

# Windows whose next placeholder-round output should be swallowed. Keyed by
# tmux window id (known at launch time, before the transcript is read — avoids
# the session-id race where the placeholder turn lands before the hook fires).
_armed_windows: set[str] = set()


def arm(window_id: str) -> None:
    """Mark ``window_id`` as awaiting (and suppressing) a placeholder round."""
    if window_id:
        _armed_windows.add(window_id)


def is_armed(window_id: str) -> bool:
    """True while ``window_id`` is still suppressing placeholder output."""
    return window_id in _armed_windows


def disarm(window_id: str) -> None:
    """Stop suppressing; idempotent."""
    _armed_windows.discard(window_id)
