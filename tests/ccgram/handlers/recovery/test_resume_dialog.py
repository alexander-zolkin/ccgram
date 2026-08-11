"""Tests for the resume-size dialog guard (CCGRAM-HOTFIX:resume-summary-dialog).

Claude Code's resume-size menu is a *menu*, not a prompt: text typed into it is
swallowed and the trailing Enter confirms its highlighted default, "Resume from
summary" — a compaction nobody asked for. These cover the pure detector and the
dismissal state machine (menu answered, menu already gone, menu that needs the
Escape fallback, menu that never clears). No tmux / Telegram needed.
"""

import pytest

from ccgram.handlers.recovery import resume_dialog
from ccgram.handlers.recovery.resume_dialog import (
    dismiss_resume_dialog,
    is_resume_dialog,
)

_DIALOG = """\
  This session is 3d 22h old and 124.1k tokens.

  Resuming the full session will consume a substantial portion of your usage
  limits. We recommend resuming from a summary.

  ❯ 1. Resume from summary (recommended)
    2. Resume full session as-is
    3. Don't ask me again

  Enter to confirm · Esc to cancel
"""

_READY = """\
  ⎿  Read scripts/vpn_speed_check.py (249 lines)

────────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────────
  ⏵⏵ auto mode on (shift+tab to cycle) · ← for agents
"""


class _FakeTmux:
    """A pane showing the menu until ``clear_key`` is pressed.

    Models the real thing: the menu stays on screen until the keystroke that
    dismisses it lands, so a test can't accidentally pass by running out of
    scripted frames.
    """

    def __init__(self, *, showing_dialog: bool, clear_key: str | None):
        self._showing = showing_dialog
        self._clear_key = clear_key
        self.keys: list[str] = []

    async def capture_pane(self, window_id):  # noqa: ARG002
        return _DIALOG if self._showing else _READY

    async def send_keys(self, window_id, text, **kwargs):  # noqa: ARG002
        self.keys.append(text)
        if self._clear_key is not None and text == self._clear_key:
            self._showing = False
        return True


@pytest.fixture()
def fake_tmux(monkeypatch):
    def _install(*, showing_dialog=True, clear_key="Enter"):
        fake = _FakeTmux(showing_dialog=showing_dialog, clear_key=clear_key)
        monkeypatch.setattr(resume_dialog, "tmux_manager", fake)
        return fake

    return _install


class TestIsResumeDialog:
    def test_detects_the_menu(self):
        assert is_resume_dialog(_DIALOG) is True

    def test_ignores_a_ready_prompt(self):
        assert is_resume_dialog(_READY) is False

    def test_empty_capture_is_not_a_dialog(self):
        assert is_resume_dialog("") is False
        assert is_resume_dialog(None) is False

    def test_needs_both_labels_so_chatter_cannot_trip_it(self):
        # A session *talking about* the feature must not be mistaken for it.
        chatter = "I told it to resume from summary and it compacted everything"
        assert is_resume_dialog(chatter) is False


@pytest.mark.asyncio
class TestDismissResumeDialog:
    async def test_no_dialog_sends_nothing(self, fake_tmux):
        fake = fake_tmux(showing_dialog=False)
        assert await dismiss_resume_dialog("@1") is False
        assert fake.keys == []  # must not disturb a live prompt

    async def test_selects_resume_full_session_as_is(self, fake_tmux):
        fake = fake_tmux(clear_key="Enter")
        assert await dismiss_resume_dialog("@1") is True
        # Down moves off the preselected "Resume from summary" onto option 2.
        assert fake.keys == ["Down", "Enter"]

    async def test_escape_fallback_when_enter_does_not_take(self, fake_tmux):
        # Enter is ignored (a repaint ate it); only Escape clears the menu.
        fake = fake_tmux(clear_key="Escape")
        assert await dismiss_resume_dialog("@1", settle_timeout=0.5) is True
        assert fake.keys == ["Down", "Enter", "Escape"]

    async def test_reports_failure_when_menu_never_clears(self, fake_tmux):
        fake = fake_tmux(clear_key=None)
        assert await dismiss_resume_dialog("@1", settle_timeout=0.5) is False
        assert fake.keys == ["Down", "Enter", "Escape"]
