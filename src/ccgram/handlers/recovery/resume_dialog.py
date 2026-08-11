"""Answer Claude Code's resume-size dialog instead of typing into it.

CCGRAM-HOTFIX:resume-summary-dialog

Claude Code (>= 2.1.216) renders a blocking TUI menu when a *resumed* session
is both older than ``CLAUDE_CODE_RESUME_THRESHOLD_MINUTES`` (default 70) and
heavier than ``CLAUDE_CODE_RESUME_TOKEN_THRESHOLD`` (default 100_000 tokens)::

    This session is 3d 22h old and 124.1k tokens.

    Resuming the full session will consume a substantial portion of your usage
    limits. We recommend resuming from a summary.

    ❯ 1. Resume from summary (recommended)
      2. Resume full session as-is
      3. Don't ask me again

    Enter to confirm · Esc to cancel

ccgram's hibernate/wake cycle hits *both* thresholds on every wake of a real
working topic (the idle hibernator only fires after 48h, and any topic worth
resuming is well past 100k tokens). Autoresume used to type the forwarded
Telegram message straight into this menu, which is not a text field:

  - the menu swallowed the message text, and
  - the trailing Enter confirmed the highlighted default — "Resume from
    summary" — which runs a full compaction.

Net effect, once per wake: an unrequested compact (recorded in the transcript
as a bare ``/compact`` prompt with ``trigger: "manual"``) plus a silently
dropped user message that the user then had to send a second time.

Prevention lives in the launch path, which exports a sky-high
``CLAUDE_CODE_RESUME_TOKEN_THRESHOLD`` so the dialog never renders. This module
is the belt-and-braces guard for windows launched without that env (a tmux
server predating the fix, adopted windows) and for the day the knob is renamed:
we pick "Resume full session as-is", because a topic wake is supposed to
preserve the topic's context — summarising it is exactly the data loss we are
trying to avoid.
"""

from __future__ import annotations

import asyncio

import structlog

from ...multiplexer import multiplexer as tmux_manager

logger = structlog.get_logger(__name__)

__all__ = ["is_resume_dialog", "dismiss_resume_dialog"]

# Both labels must be present: the pair is unique to this menu, so a topic that
# merely *discusses* resuming can't trip the detector.
_MARKERS = ("resume from summary", "resume full session as-is")

# Time budget for the menu to disappear after we answer it.
_SETTLE_TIMEOUT_S = 10.0
_SETTLE_POLL_S = 0.25


def is_resume_dialog(pane_text: str | None) -> bool:
    """True when ``pane_text`` shows Claude Code's resume-size menu."""
    if not pane_text:
        return False
    haystack = pane_text.lower()
    return all(marker in haystack for marker in _MARKERS)


async def dismiss_resume_dialog(
    window_id: str,
    *,
    settle_timeout: float = _SETTLE_TIMEOUT_S,
) -> bool:
    """Select "Resume full session as-is" if the resume menu is showing.

    Returns True only when the menu was found *and* it went away, i.e. when the
    caller may now type a prompt. Returns False when no menu was showing (the
    normal case — nothing was sent, so the caller proceeds unchanged).

    Deliberately does a single capture rather than polling for the menu to
    appear: callers run this immediately before forwarding text, several
    seconds after the session-start hook, by which point Claude Code has long
    since rendered it. Polling would tax every wake to catch a case the launch
    env already prevents.
    """
    pane = await tmux_manager.capture_pane(window_id)
    if not is_resume_dialog(pane):
        return False

    # Option 1 ("Resume from summary") is preselected; step down to option 2.
    await tmux_manager.send_keys(window_id, "Down", enter=False, literal=False)
    await asyncio.sleep(0.15)
    await tmux_manager.send_keys(window_id, "Enter", enter=False, literal=False)
    logger.info(
        "Resume-size dialog answered 'Resume full session as-is' for window %s",
        window_id,
    )

    if await _wait_until_gone(window_id, settle_timeout):
        return True

    # Down+Enter did not take (a repaint can land between the two keys). The
    # menu's own escape hatch dismisses it without compacting, so try that
    # before giving up — anything is better than leaving a live menu for the
    # caller to type into.
    logger.warning(
        "Resume-size dialog still showing %.0fs after answering it; sending Escape"
        " (window %s)",
        settle_timeout,
        window_id,
    )
    await tmux_manager.send_keys(window_id, "Escape", enter=False, literal=False)
    if await _wait_until_gone(window_id, settle_timeout):
        return True

    logger.error("Resume-size dialog could not be dismissed for window %s", window_id)
    return False


async def _wait_until_gone(window_id: str, timeout: float) -> bool:
    """Poll until the resume menu is no longer on screen (or ``timeout``)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await asyncio.sleep(_SETTLE_POLL_S)
        if not is_resume_dialog(await tmux_manager.capture_pane(window_id)):
            return True
    return False
