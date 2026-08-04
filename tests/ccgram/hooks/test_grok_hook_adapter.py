"""Tests for the Grok hook adapter, install, and transcript resolution."""

from __future__ import annotations

import json

from ccgram.hooks.adapters import (
    detect_provider_from_payload,
    get_hook_adapter,
)

_SID = "019f702f-cf7c-73b2-a601-430d0146acb7"


def _adapter():
    adapter = get_hook_adapter("grok")
    assert adapter is not None
    return adapter


def test_grok_is_a_safe_provider() -> None:
    assert get_hook_adapter("grok") is not None


def test_normalize_session_start_camelcase() -> None:
    ev = _adapter().normalize(
        {
            "hookEventName": "session_start",
            "sessionId": _SID,
            "cwd": "/tmp/proj",
            "workspaceRoot": "/tmp/proj",
            "source": "startup",
        }
    )
    assert ev is not None
    assert ev.provider_name == "grok"
    assert ev.canonical_event_name == "SessionStart"
    assert ev.session_id == _SID
    assert str(ev.cwd) == "/tmp/proj"
    assert ev.data["source"] == "startup"


def test_normalize_uses_workspace_root_when_cwd_absent() -> None:
    ev = _adapter().normalize(
        {"hookEventName": "session_start", "sessionId": _SID, "workspaceRoot": "/ws"}
    )
    assert ev is not None
    assert str(ev.cwd) == "/ws"


def test_normalize_stop_reason() -> None:
    ev = _adapter().normalize(
        {"hookEventName": "stop", "sessionId": _SID, "stopReason": "end_turn"}
    )
    assert ev is not None
    assert ev.canonical_event_name == "Stop"
    assert ev.data["stop_reason"] == "end_turn"


def test_normalize_notification() -> None:
    ev = _adapter().normalize(
        {
            "hookEventName": "notification",
            "sessionId": _SID,
            "message": "done",
            "notificationType": "idle",
        }
    )
    assert ev is not None
    assert ev.canonical_event_name == "Notification"
    assert ev.data["message"] == "done"
    assert ev.data["notification_type"] == "idle"


def test_normalize_session_end_and_subagent() -> None:
    adapter = _adapter()
    end = adapter.normalize(
        {"hookEventName": "session_end", "sessionId": _SID, "reason": "quit"}
    )
    assert end is not None and end.canonical_event_name == "SessionEnd"
    assert end.data["reason"] == "quit"

    sub = adapter.normalize(
        {"hookEventName": "subagent_stop", "sessionId": _SID, "name": "planner"}
    )
    assert sub is not None and sub.canonical_event_name == "SubagentStop"
    assert sub.data["name"] == "planner"


def test_normalize_accepts_pascalcase_passthrough() -> None:
    ev = _adapter().normalize(
        {"hookEventName": "SessionStart", "sessionId": _SID, "cwd": "/x"}
    )
    assert ev is not None
    assert ev.canonical_event_name == "SessionStart"


def test_normalize_rejects_bad_session_id() -> None:
    assert (
        _adapter().normalize({"hookEventName": "stop", "sessionId": "not-a-uuid"})
        is None
    )


def test_normalize_rejects_unknown_event() -> None:
    assert _adapter().normalize({"hookEventName": "bananas", "sessionId": _SID}) is None


def test_installable_events() -> None:
    events = set(_adapter().installable_events)
    assert {
        "SessionStart",
        "Stop",
        "StopFailure",
        "SessionEnd",
        "Notification",
    } <= events


def test_detect_provider_from_payload_grok() -> None:
    assert (
        detect_provider_from_payload(
            {"hookEventName": "session_start", "sessionId": _SID, "workspaceRoot": "/x"}
        )
        == "grok"
    )
    assert (
        detect_provider_from_payload(
            {"transcript_path": "/home/u/.grok/sessions/%2Froot/a/chat_history.jsonl"}
        )
        == "grok"
    )


# ── Install / status ──────────────────────────────────────────────────────


def test_install_writes_grok_hooks_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    from ccgram.hook import _install_hook, _grok_hooks_file

    rc = _install_hook("grok")
    assert rc == 0
    hooks_file = _grok_hooks_file()
    assert hooks_file == tmp_path / "hooks" / "ccgram.json"
    data = json.loads(hooks_file.read_text())
    assert "SessionStart" in data["hooks"]
    assert "Stop" in data["hooks"]
    entry = data["hooks"]["SessionStart"][0]["hooks"][0]
    assert "hook --provider grok" in entry["command"]
    assert entry["type"] == "command"


def test_install_is_idempotent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    from ccgram.hook import _install_hook, _grok_hooks_file

    assert _install_hook("grok") == 0
    assert _install_hook("grok") == 0  # second run: already present, no error
    data = json.loads(_grok_hooks_file().read_text())
    # No duplicate hook entries for SessionStart.
    assert len(data["hooks"]["SessionStart"]) == 1


# ── Transcript path reconstruction ────────────────────────────────────────


def test_resolve_grok_transcript_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    from ccgram.hook import _encode_grok_cwd_dirname, _resolve_grok_transcript_path

    sdir = tmp_path / "sessions" / _encode_grok_cwd_dirname("/tmp/proj") / _SID
    sdir.mkdir(parents=True)
    (sdir / "chat_history.jsonl").write_text("{}\n")
    resolved = _resolve_grok_transcript_path(_SID, "/tmp/proj")
    assert resolved == str(sdir / "chat_history.jsonl")


def test_resolve_grok_transcript_path_by_id_scan(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    from ccgram.hook import _resolve_grok_transcript_path

    # Session under a slug/hash group (cwd doesn't encode to this dir name).
    sdir = tmp_path / "sessions" / "slug-abc123" / _SID
    sdir.mkdir(parents=True)
    (sdir / "chat_history.jsonl").write_text("{}\n")
    resolved = _resolve_grok_transcript_path(_SID, "/some/long/path")
    assert resolved.endswith(f"{_SID}/chat_history.jsonl")
