"""Tests for the Grok Build provider (transcript, discovery, launch, commands)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ccgram.providers import (
    _ensure_registered,
    detect_provider_from_command,
    detect_provider_from_transcript_path,
    has_yolo_mode,
    registry,
    resolve_launch_command,
)
from ccgram.providers.base import AgentMessage
from ccgram.providers.grok import GrokProvider
from ccgram.providers.grok_discovery import (
    encode_cwd_dirname,
    discover_session_for_cwd,
)
from ccgram.providers.process_detection import classify_provider_from_argv

_FIXTURE = Path(__file__).parent / "fixtures" / "grok_chat_history.jsonl"


def _parse_fixture(provider: GrokProvider) -> list[AgentMessage]:
    entries = []
    for line in _FIXTURE.read_text(encoding="utf-8").splitlines():
        parsed = provider.parse_transcript_line(line)
        if parsed is not None:
            entries.append(parsed)
    messages, _pending = provider.parse_transcript_entries(entries, {})
    return messages


# ── Registration / capabilities ──────────────────────────────────────────


def test_registered_in_registry() -> None:
    _ensure_registered()
    assert "grok" in registry.provider_names()
    assert isinstance(registry.get("grok"), GrokProvider)


def test_capabilities() -> None:
    caps = GrokProvider().capabilities
    assert caps.name == "grok"
    assert caps.launch_command == "grok"
    assert caps.supports_hook is True
    assert caps.supports_resume is True
    assert caps.supports_continue is True
    assert caps.supports_structured_transcript is True
    assert caps.supports_status_snapshot is True
    assert caps.supports_model_picker is True
    assert "model" in caps.tui_picker_commands


def test_yolo_flag_registration() -> None:
    assert has_yolo_mode("grok") is True
    assert resolve_launch_command("grok") == "grok"
    assert (
        resolve_launch_command("grok", approval_mode="yolo") == "grok --always-approve"
    )


def test_picker_commands_subset_of_builtins() -> None:
    caps = GrokProvider().capabilities
    builtin = {c.lstrip("/") for c in caps.builtin_commands}
    assert caps.tui_picker_commands <= builtin


# ── Launch args ───────────────────────────────────────────────────────────


def test_make_launch_args_fresh() -> None:
    assert GrokProvider().make_launch_args() == ""


def test_make_launch_args_resume() -> None:
    sid = "019f702f-cf7c-73b2-a601-430d0146acb7"
    assert GrokProvider().make_launch_args(resume_id=sid) == f"--resume {sid}"


def test_make_launch_args_continue() -> None:
    assert GrokProvider().make_launch_args(use_continue=True) == "--continue"


def test_make_launch_args_rejects_shell_metachars() -> None:
    with pytest.raises(ValueError):
        GrokProvider().make_launch_args(resume_id="abc; rm -rf /")


# ── Transcript parsing ────────────────────────────────────────────────────


def test_transcript_skips_system_and_synthetic_user() -> None:
    messages = _parse_fixture(GrokProvider())
    user_msgs = [m for m in messages if m.role == "user"]
    # Only the real <user_query> turn survives; user_info + system-reminder drop.
    assert len(user_msgs) == 1
    assert user_msgs[0].text == "list the files then say done"


def test_transcript_reasoning_relayed_without_encrypted_blob() -> None:
    messages = _parse_fixture(GrokProvider())
    thinking = [m for m in messages if m.content_type == "thinking"]
    assert len(thinking) == 1
    assert "confirm" in thinking[0].text
    # The encrypted blob must never appear in any relayed message.
    assert all("SECRET_BLOB_MUST_NOT_LEAK" not in m.text for m in messages)


def test_transcript_assistant_text_and_tool_calls() -> None:
    messages = _parse_fixture(GrokProvider())
    tool_uses = [m for m in messages if m.content_type == "tool_use"]
    names = {m.tool_name for m in tool_uses}
    # list_dir → List, run_terminal_command → Bash, web_search → WebSearch.
    assert {"List", "Bash", "WebSearch"} <= names
    assert any(m.text == "Проверю файлы." for m in messages)
    assert any(m.text == "Done" for m in messages)


def test_transcript_tool_results_resolve_pending() -> None:
    provider = GrokProvider()
    entries = [
        provider.parse_transcript_line(line)
        for line in _FIXTURE.read_text().splitlines()
    ]
    entries = [e for e in entries if e is not None]
    _messages, pending = provider.parse_transcript_entries(entries, {})
    # Both client tool calls were resolved by their tool_result lines.
    assert "call-1" not in pending
    assert "call-2" not in pending


def test_tool_result_carries_tool_name() -> None:
    messages = _parse_fixture(GrokProvider())
    results = [m for m in messages if m.content_type == "tool_result"]
    assert results
    assert {"List", "Bash"} <= {m.tool_name for m in results}


def test_is_user_transcript_entry() -> None:
    provider = GrokProvider()
    assert provider.is_user_transcript_entry(
        {"type": "user", "content": [{"type": "text", "text": "hello"}]}
    )
    assert not provider.is_user_transcript_entry(
        {"type": "user", "content": "x", "synthetic_reason": "setup"}
    )
    assert not provider.is_user_transcript_entry(
        {"type": "user", "content": [{"type": "text", "text": "<user_info>\nx"}]}
    )
    assert not provider.is_user_transcript_entry({"type": "assistant", "content": "hi"})


def test_parse_history_entry_unwraps_user_query() -> None:
    provider = GrokProvider()
    msg = provider.parse_history_entry(
        {
            "type": "user",
            "content": [{"type": "text", "text": "<user_query>\nhi\n</user_query>"}],
        }
    )
    assert msg is not None
    assert msg.role == "user"
    assert msg.text == "hi"


# ── cwd encoding + discovery ──────────────────────────────────────────────


def test_encode_cwd_dirname() -> None:
    assert encode_cwd_dirname("/root") == "%2Froot"
    assert encode_cwd_dirname("/home/x/proj") == "%2Fhome%2Fx%2Fproj"
    # Trailing separators collide with the un-suffixed form.
    assert encode_cwd_dirname("/a/b/") == encode_cwd_dirname("/a/b")


def _make_session(root: Path, cwd: str, session_id: str) -> Path:
    sdir = root / "sessions" / encode_cwd_dirname(cwd) / session_id
    sdir.mkdir(parents=True)
    (sdir / "summary.json").write_text(
        json.dumps(
            {"info": {"id": session_id, "cwd": cwd}, "current_model_id": "grok-4.5"}
        )
    )
    (sdir / "chat_history.jsonl").write_text('{"type":"user","content":"hi"}\n')
    return sdir


def test_discover_session_for_cwd(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    proj = tmp_path / "proj"
    proj.mkdir()
    sid = "019f702f-cf7c-73b2-a601-430d0146acb7"
    _make_session(tmp_path, str(proj), sid)
    found = discover_session_for_cwd(str(proj), max_age=0)
    assert found is not None
    got_sid, got_cwd, chat = found
    assert got_sid == sid
    assert Path(got_cwd) == proj
    assert chat.name == "chat_history.jsonl"


def test_discover_transcript_returns_event(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    proj = tmp_path / "proj"
    proj.mkdir()
    sid = "019f702f-caf8-7a73-b40e-acabc54b12d1"
    _make_session(tmp_path, str(proj), sid)
    ev = GrokProvider().discover_transcript(str(proj), "ccgram:@1", max_age=0)
    assert ev is not None
    assert ev.session_id == sid
    assert ev.window_key == "ccgram:@1"
    assert ev.transcript_path.endswith("chat_history.jsonl")


def test_discover_transcript_missing_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    assert GrokProvider().discover_transcript("/nonexistent", "ccgram:@9") is None


# ── Status snapshot ───────────────────────────────────────────────────────


def test_status_snapshot(tmp_path) -> None:
    sdir = tmp_path / "sess"
    sdir.mkdir()
    (sdir / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "sess-1", "cwd": "/tmp/proj"},
                "current_model_id": "grok-4.5",
                "reasoning_effort": "high",
                "num_chat_messages": 12,
                "generated_title": "Test session",
            }
        )
    )
    (sdir / "signals.json").write_text(
        json.dumps(
            {
                "contextTokensUsed": 5000,
                "contextWindowTokens": 500000,
                "turnCount": 3,
                "toolCallCount": 2,
            }
        )
    )
    (sdir / "chat_history.jsonl").write_text('{"type":"assistant","content":"hi"}\n')
    snap = GrokProvider().build_status_snapshot(
        str(sdir / "chat_history.jsonl"),
        display_name="proj",
        session_id="sess-1",
        cwd="/tmp/proj",
    )
    assert snap is not None
    assert "proj" in snap
    assert "sess-1" in snap
    assert "grok-4.5" in snap
    assert "Test session" in snap


def test_status_snapshot_missing_returns_none() -> None:
    assert (
        GrokProvider().build_status_snapshot(
            "/nope/chat_history.jsonl", display_name="x"
        )
        is None
    )


def test_has_output_since(tmp_path) -> None:
    path = tmp_path / "chat_history.jsonl"
    path.write_text('{"type":"assistant","content":"hello"}\n')
    assert GrokProvider().has_output_since(str(path), 0) is True
    # A user-only transcript has no assistant output.
    path.write_text('{"type":"user","content":"hi"}\n')
    assert GrokProvider().has_output_since(str(path), 0) is False


# ── Detection ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "grok",
        "kara_grok",
        "/usr/local/bin/grok --always-approve",
        "/root/.grok/bin/grok",
    ],
)
def test_detect_provider_from_command(command: str) -> None:
    assert detect_provider_from_command(command) == "grok"


def test_detect_provider_from_transcript_path() -> None:
    assert (
        detect_provider_from_transcript_path(
            "/home/u/.grok/sessions/%2Froot/abc/chat_history.jsonl"
        )
        == "grok"
    )


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["/root/.grok/bin/grok", "--always-approve"], "grok"),
        (["node", "/root/.grok/bin/grok"], "grok"),
        (["kara_grok"], "grok"),
        # The npm @vibe-kit/grok-cli is a node script — its argv0 is a JS file.
        (["node", "/usr/lib/node_modules/@vibe-kit/grok-cli/dist/cli.js"], ""),
    ],
)
def test_classify_provider_from_argv(argv: list[str], expected: str) -> None:
    assert classify_provider_from_argv(argv) == expected


# ── Commands ──────────────────────────────────────────────────────────────


def test_discover_commands_includes_model_and_effort() -> None:
    commands = GrokProvider().discover_commands("/tmp/whatever")
    names = {c.name for c in commands}
    assert "/model" in names
    assert "/effort" in names
    assert all(c.source == "builtin" for c in commands)


# ── Whole-file transcript reading (grok rewrites chat_history.jsonl) ────────


def test_grok_is_not_incremental() -> None:
    # Grok rewrites its transcript → must be read whole-file, not by byte offset.
    assert GrokProvider().capabilities.supports_incremental_read is False


def test_read_transcript_file_line_indexed(tmp_path) -> None:
    p = tmp_path / "chat_history.jsonl"
    p.write_text(
        '{"type":"user","content":"a"}\n{"type":"assistant","content":"b"}\n',
        encoding="utf-8",
    )
    prov = GrokProvider()
    entries, offset = prov.read_transcript_file(str(p), 0)
    assert offset == 2
    assert [e["type"] for e in entries] == ["user", "assistant"]
    # Nothing new since the last index.
    new, off2 = prov.read_transcript_file(str(p), 2)
    assert new == []
    assert off2 == 2


def test_read_transcript_file_survives_non_utf8(tmp_path) -> None:
    # A stale byte offset used to crash the reader with UnicodeDecodeError; the
    # whole-file reader must tolerate stray bytes without raising.
    p = tmp_path / "chat_history.jsonl"
    p.write_bytes(
        b'{"type":"assistant","content":"\xd0\xbf\xd1\x80\xd0\xb8"}\n'
        b"\x8f\x8f garbage\n"
        b'{"type":"user","content":"x"}\n'
    )
    prov = GrokProvider()
    entries, offset = prov.read_transcript_file(str(p), 0)
    assert offset == 2  # garbage line dropped, two valid entries kept
    assert any(e.get("type") == "assistant" for e in entries)


def test_read_transcript_file_reset_on_shrink(tmp_path) -> None:
    p = tmp_path / "chat_history.jsonl"
    p.write_text('{"type":"user","content":"a"}\n', encoding="utf-8")
    prov = GrokProvider()
    # last_offset beyond the current entry count → re-read from the top.
    entries, offset = prov.read_transcript_file(str(p), 99)
    assert offset == 1
    assert len(entries) == 1
