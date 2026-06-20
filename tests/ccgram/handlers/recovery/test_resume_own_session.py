"""Tests for resume-own-session: id-scoped autoresume (CCGRAM-HOTFIX:resume-own-session).

Covers the pure launch-args decision (``decide_launch_args``) and the
events.jsonl recovery helper (``_recover_session_id_for_window``) — the two
pieces that decide whether autoresume resumes a topic's OWN session by id or
falls back to cwd-newest ``--continue``. No tmux / Telegram needed.
"""

import json

import pytest

from ccgram.providers.claude import ClaudeProvider
from ccgram.handlers.recovery.recovery_banner import (
    _recover_session_id_for_window,
    decide_launch_args,
)

_VALID = "3e713e04-4280-4503-9b4b-50ef16750d6e"
_OTHER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture()
def claude() -> ClaudeProvider:
    return ClaudeProvider()


class TestDecideLaunchArgs:
    def test_own_session_uncontended_resumes_by_id(self, claude):
        args, arm = decide_launch_args(claude, _VALID, True, False, _OTHER, False)
        assert args == f"--resume {_VALID}"
        assert arm is False  # --resume must NOT arm synthetic suppression

    def test_own_session_contended_bails_to_banner(self, claude):
        args, arm = decide_launch_args(claude, _VALID, True, True, _OTHER, False)
        assert args is None  # genuine collision -> recovery banner

    def test_no_own_session_falls_back_to_continue(self, claude):
        args, arm = decide_launch_args(claude, "", False, False, _OTHER, False)
        assert args == "--continue"
        assert arm is True  # --continue arms (synthetic placeholder round)

    def test_cwd_collision_on_fallback_bails_to_banner(self, claude):
        args, arm = decide_launch_args(claude, "", False, False, _OTHER, True)
        assert args is None

    def test_own_transcript_missing_falls_back_to_continue(self, claude):
        # own_sid known but its transcript is gone from disk -> don't --resume a
        # missing session; fall back to --continue.
        args, arm = decide_launch_args(claude, _VALID, False, False, _OTHER, False)
        assert args == "--continue"
        assert arm is True

    def test_malformed_own_sid_falls_back_not_crashes(self, claude):
        # transcript "ok" but the id is not a UUID -> make_launch_args raises,
        # decide swallows it and falls back to --continue (never propagates).
        args, arm = decide_launch_args(claude, "not-a-uuid", True, False, _OTHER, False)
        assert args == "--continue"
        assert arm is True

    def test_own_equals_cwd_newest_still_resumes_by_id(self, claude):
        # The common single-topic case: own session IS the cwd-newest. Resuming
        # by id yields the same session, harmlessly.
        args, arm = decide_launch_args(claude, _VALID, True, False, _VALID, False)
        assert args == f"--resume {_VALID}"
        assert arm is False

    def test_no_own_no_candidate_uses_continue(self, claude):
        args, arm = decide_launch_args(claude, "", False, False, "", False)
        assert args == "--continue"
        assert arm is True


class TestRecoverSessionId:
    @staticmethod
    def _write(path, rows):
        path.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )

    def test_recovers_latest_paired_sid_and_transcript(self, tmp_path, monkeypatch):
        ev = tmp_path / "events.jsonl"
        self._write(
            ev,
            [
                {
                    "window_key": "ccgram:@5",
                    "session_id": _OTHER,
                    "data": {"transcript_path": "/x/old.jsonl"},
                },
                {
                    "window_key": "ccgram:@5",
                    "session_id": _VALID,
                    "data": {"transcript_path": "/x/new.jsonl"},
                },
                {  # different window — must be ignored
                    "window_key": "ccgram:@9",
                    "session_id": "zzz",
                    "data": {"transcript_path": "/y/other.jsonl"},
                },
            ],
        )
        monkeypatch.setenv("CCGRAM_DIR", str(tmp_path))
        assert _recover_session_id_for_window("@5") == (_VALID, "/x/new.jsonl")

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CCGRAM_DIR", str(tmp_path))  # no events.jsonl present
        assert _recover_session_id_for_window("@5") == ("", "")

    def test_bad_json_line_is_skipped(self, tmp_path, monkeypatch):
        ev = tmp_path / "events.jsonl"
        good = json.dumps(
            {
                "window_key": "ccgram:@5",
                "session_id": _VALID,
                "data": {"transcript_path": "/x/new.jsonl"},
            }
        )
        ev.write_text("{not valid json\n" + good + "\n", encoding="utf-8")
        monkeypatch.setenv("CCGRAM_DIR", str(tmp_path))
        assert _recover_session_id_for_window("@5") == (_VALID, "/x/new.jsonl")

    def test_event_without_transcript_not_paired(self, tmp_path, monkeypatch):
        # An event with a session_id but no transcript_path must NOT yield a
        # half-pair (we need both to safely --resume).
        ev = tmp_path / "events.jsonl"
        self._write(
            ev, [{"window_key": "ccgram:@5", "session_id": _VALID, "data": {}}]
        )
        monkeypatch.setenv("CCGRAM_DIR", str(tmp_path))
        assert _recover_session_id_for_window("@5") == ("", "")
