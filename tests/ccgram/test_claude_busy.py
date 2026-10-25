"""CCGRAM-HOTFIX:claude-registry-busy — Claude Code's registry keeps a
streaming reply's window active (no spinner on screen meanwhile)."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

from ccgram import claude_busy
from ccgram.handlers.polling.window_tick import observe


def _registry(tmp_path, monkeypatch, entries):
    d = tmp_path / "claude" / "sessions"
    d.mkdir(parents=True)
    for i, e in enumerate(entries):
        (d / f"{i}.json").write_text(json.dumps(e))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    claude_busy.reset_for_testing()


def test_busy_sessions_are_the_live_busy_ones(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, [
        {"pid": os.getpid(), "sessionId": "working", "status": "busy"},
        {"pid": os.getpid(), "sessionId": "waiting", "status": "idle"},
        {"pid": 2**22 + 12345, "sessionId": "gone", "status": "busy"},  # no such process
    ])
    assert claude_busy.busy_sessions() == {"working"}
    assert claude_busy.is_busy("working") and not claude_busy.is_busy("waiting") and not claude_busy.is_busy(None)


def test_a_streaming_reply_keeps_the_window_active(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, [{"pid": os.getpid(), "sessionId": "sid-1", "status": "busy"}])
    claude = SimpleNamespace(capabilities=SimpleNamespace(name="claude", supports_hook=True))
    monkeypatch.setattr(observe, "_get_provider", lambda wid: claude)
    monkeypatch.setattr(observe, "_get_last_activity_ts", lambda wid: None)
    monkeypatch.setattr(observe.window_query, "get_session_id_for_window", lambda wid: "sid-1")
    w = SimpleNamespace(pane_current_command="claude")
    ctx = observe.build_context("@busy-test", w, None)
    assert ctx.is_recently_active is True
    # an idle registry entry: the screen decides as before
    _registry(tmp_path / "b", monkeypatch, [{"pid": os.getpid(), "sessionId": "sid-1", "status": "idle"}])
    assert observe.build_context("@idle-test", w, None).is_recently_active is False
    # another provider is not asked
    codex = SimpleNamespace(capabilities=SimpleNamespace(name="codex", supports_hook=False))
    _registry(tmp_path / "c", monkeypatch, [{"pid": os.getpid(), "sessionId": "sid-1", "status": "busy"}])
    monkeypatch.setattr(observe, "_get_provider", lambda wid: codex)
    assert observe.build_context("@codex-test", w, None).is_recently_active is False
