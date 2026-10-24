"""Tests for transcript reader offset handling."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ccgram.idle_tracker import IdleTracker
from ccgram.monitor_state import BacklogSkipIntent, MonitorState, TrackedSession
from ccgram.transcript_reader import TranscriptReader, _StableRead


def test_clear_session_cancels_pending_skip_barrier(tmp_path) -> None:
    state = MonitorState(state_file=tmp_path / "monitor.json")
    state.update_session(TrackedSession("sess", str(tmp_path / "session.jsonl")))
    state.begin_skip(BacklogSkipIntent("sess", "@1", 1, 4, 99, 10, 0))
    reader = TranscriptReader(state, IdleTracker())

    reader.clear_session("sess")

    assert "sess" not in state.pending_skips
    assert state.get_session("sess") is None


async def test_same_transcript_reuses_offset_after_session_map_refresh(
    tmp_path,
) -> None:
    """A tmux rename/session-map refresh must not replay an existing transcript."""
    first = (
        '{"type":"assistant","message":{"content":[{"type":"text","text":"old"}]}}\n'
    )
    second = (
        '{"type":"assistant","message":{"content":[{"type":"text","text":"new"}]}}\n'
    )
    session_file = tmp_path / "transcript.jsonl"
    session_file.write_text(first + second, newline="\n")

    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    state.update_session(
        TrackedSession(
            session_id="sess-before-rename",
            file_path=str(session_file),
            last_byte_offset=len(first.encode()),
        )
    )
    state.begin_skip(BacklogSkipIntent("sess-before-rename", "@1", 1, 4, 99, 50, 0))
    reader = TranscriptReader(state, IdleTracker())

    messages = []
    await reader._process_session_file(
        "sess-after-rename",
        session_file,
        messages,
        window_id="@1",
    )

    assert [msg.text for msg in messages] == ["new"]
    tracked = state.get_session("sess-after-rename")
    assert tracked is not None
    assert tracked.parsed_offset == session_file.stat().st_size
    assert "sess-before-rename" not in state.pending_skips


async def test_catch_up_read_after_restart_is_not_activity(tmp_path) -> None:
    old = '{"type":"assistant","message":{"content":[{"type":"text","text":"a"}]}}\n'
    unread = '{"type":"assistant","message":{"content":[{"type":"text","text":"b"}]}}\n'
    session_file = tmp_path / "transcript.jsonl"
    session_file.write_text(old + unread, newline="\n")

    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    state.update_session(
        TrackedSession(
            session_id="sess",
            file_path=str(session_file),
            last_byte_offset=len(old.encode()),
        )
    )
    idle = IdleTracker()
    reader = TranscriptReader(state, idle)

    messages = []
    await reader._process_session_file("sess", session_file, messages, window_id="@1")

    assert [msg.text for msg in messages] == ["b"]
    assert idle.get_last_activity("sess") is None


async def test_first_poll_counts_only_bytes_written_after_startup(tmp_path) -> None:
    old = '{"type":"assistant","message":{"content":[{"type":"text","text":"a"}]}}\n'
    unread = '{"type":"assistant","message":{"content":[{"type":"text","text":"b"}]}}\n'
    fresh = '{"type":"assistant","message":{"content":[{"type":"text","text":"c"}]}}\n'
    session_file = tmp_path / "transcript.jsonl"
    session_file.write_text(old + unread, newline="\n")

    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    state.update_session(
        TrackedSession(
            session_id="sess",
            file_path=str(session_file),
            last_byte_offset=len(old.encode()),
        )
    )
    idle = IdleTracker()
    reader = TranscriptReader(state, idle)
    with session_file.open("a") as transcript:
        transcript.write(fresh)

    messages = []
    await reader._process_session_file("sess", session_file, messages, window_id="@1")

    assert [msg.text for msg in messages] == ["b", "c"]
    assert idle.get_last_activity("sess") is not None


async def test_post_start_truncation_clamps_to_eof_without_replay(tmp_path) -> None:
    old = '{"type":"assistant","message":{"content":[{"type":"text","text":"old"}]}}\n'
    history = old * 10
    fresh = (
        '{"type":"assistant","message":{"content":[{"type":"text","text":"new"}]}}\n'
    )
    session_file = tmp_path / "transcript.jsonl"
    session_file.write_text(history, newline="\n")

    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    state.update_session(
        TrackedSession(
            session_id="sess",
            file_path=str(session_file),
            last_byte_offset=len(old.encode()) * 5,
        )
    )
    idle = IdleTracker()
    reader = TranscriptReader(state, idle)
    session_file.write_text(fresh, newline="\n")

    messages = []
    await reader._process_session_file("sess", session_file, messages, window_id="@1")

    assert messages == []


async def test_atomic_replacement_after_start_clamps_to_eof(tmp_path) -> None:
    old = '{"type":"assistant","message":{"content":[{"type":"text","text":"old"}]}}\n'
    fresh = (
        '{"type":"assistant","message":{"content":[{"type":"text","text":"fresh"}]}}\n'
    )
    session_file = tmp_path / "transcript.jsonl"
    session_file.write_text(old, newline="\n")
    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    state.update_session(
        TrackedSession(
            session_id="sess", file_path=str(session_file), last_byte_offset=0
        )
    )
    reader = TranscriptReader(state, IdleTracker())
    await reader._process_session_file("sess", session_file, [], window_id="@1")

    replacement = tmp_path / "replacement.jsonl"
    replacement.write_text(fresh, newline="\n")
    replacement.replace(session_file)

    messages = []
    await reader._process_session_file("sess", session_file, messages, window_id="@1")

    assert messages == []


async def test_same_inode_rewrite_with_preserved_mtime_does_not_replay(
    tmp_path,
) -> None:
    old = '{"type":"assistant","message":{"content":[{"type":"text","text":"old"}]}}\n'
    new = old.replace("old", "new")
    session_file = tmp_path / "transcript.jsonl"
    session_file.write_text(old)
    initial_stat = session_file.stat()
    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    state.update_session(
        TrackedSession(
            session_id="sess", file_path=str(session_file), last_byte_offset=0
        )
    )
    reader = TranscriptReader(state, IdleTracker())
    await reader._process_session_file("sess", session_file, [], window_id="@1")

    session_file.write_text(new)
    os.utime(
        session_file,
        ns=(initial_stat.st_atime_ns, initial_stat.st_mtime_ns),
    )

    messages = []
    await reader._process_session_file("sess", session_file, messages, window_id="@1")

    assert messages == []


async def test_replacement_between_stat_and_open_clamps_to_eof(tmp_path) -> None:
    old = '{"type":"assistant","message":{"content":[{"type":"text","text":"old"}]}}\n'
    new = old.replace("old", "new")
    session_file = tmp_path / "transcript.jsonl"
    session_file.write_text(old)
    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    state.update_session(
        TrackedSession(
            session_id="sess", file_path=str(session_file), last_byte_offset=0
        )
    )
    reader = TranscriptReader(state, IdleTracker())
    await reader._process_session_file("sess", session_file, [], window_id="@1")
    original_read = reader._read_new_lines
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_text(new)
    session_file.write_text(old)  # Trigger the read before replacing it in-flight.
    replaced = False

    async def replace_then_read(*args, **kwargs):
        nonlocal replaced
        if not replaced:
            replacement.replace(session_file)
            replaced = True
        return await original_read(*args, **kwargs)

    messages = []
    await reader._process_session_file("sess", session_file, messages, window_id="@1")

    assert messages == []


async def test_whole_file_rewrite_bypasses_unchanged_mtime(tmp_path) -> None:
    session_file = tmp_path / "transcript.json"
    session_file.write_text("old")
    old_stat = session_file.stat()
    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    state.update_session(
        TrackedSession(
            session_id="sess", file_path=str(session_file), last_byte_offset=0
        )
    )
    reader = TranscriptReader(state, IdleTracker())
    provider = SimpleNamespace(
        capabilities=SimpleNamespace(
            name="gemini",
            supports_incremental_read=False,
            supports_task_tracking=False,
        )
    )
    with (
        patch(
            "ccgram.transcript_reader._resolve_provider_for_file", return_value=provider
        ),
        patch.object(
            reader,
            "_read_session_entries",
            new=AsyncMock(
                side_effect=lambda *_args, **_kwargs: _StableRead(
                    [], session_file.stat(), False
                )
            ),
        ) as read,
        patch.object(reader, "_append_provider_messages"),
    ):
        await reader._process_session_file("sess", session_file, [], window_id="@1")
        session_file.write_text("new-long")
        os.utime(session_file, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        await reader._process_session_file("sess", session_file, [], window_id="@1")
    assert read.await_count == 2


async def test_whole_file_replacement_bypasses_unchanged_mtime(tmp_path) -> None:
    session_file = tmp_path / "transcript.json"
    session_file.write_text("old")
    old_stat = session_file.stat()
    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    state.update_session(
        TrackedSession(
            session_id="sess", file_path=str(session_file), last_byte_offset=0
        )
    )
    reader = TranscriptReader(state, IdleTracker())
    provider = SimpleNamespace(
        capabilities=SimpleNamespace(
            name="gemini",
            supports_incremental_read=False,
            supports_task_tracking=False,
        )
    )

    with (
        patch(
            "ccgram.transcript_reader._resolve_provider_for_file", return_value=provider
        ),
        patch.object(
            reader,
            "_read_session_entries",
            new=AsyncMock(
                side_effect=lambda *_args, **_kwargs: _StableRead(
                    [],
                    session_file.stat(),
                    False,
                )
            ),
        ) as read,
        patch.object(reader, "_append_provider_messages"),
    ):
        await reader._process_session_file("sess", session_file, [], window_id="@1")
        replacement = tmp_path / "replacement.json"
        replacement.write_text("new")
        replacement.replace(session_file)
        os.utime(session_file, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))

        await reader._process_session_file("sess", session_file, [], window_id="@1")

    assert read.await_count == 2


async def test_failed_startup_read_preserves_catch_up_boundary(tmp_path) -> None:
    old = '{"type":"assistant","message":{"content":[{"type":"text","text":"a"}]}}\n'
    unread = '{"type":"assistant","message":{"content":[{"type":"text","text":"b"}]}}\n'
    session_file = tmp_path / "transcript.jsonl"
    session_file.write_text(old + unread, newline="\n")

    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    state.update_session(
        TrackedSession(
            session_id="sess",
            file_path=str(session_file),
            last_byte_offset=len(old.encode()),
        )
    )
    idle = IdleTracker()
    reader = TranscriptReader(state, idle)

    with patch.object(
        reader, "_read_new_lines", new=AsyncMock(side_effect=OSError("busy"))
    ):
        await reader._process_session_file("sess", session_file, [], window_id="@1")

    messages = []
    await reader._process_session_file("sess", session_file, messages, window_id="@1")

    assert [msg.text for msg in messages] == ["b"]
    assert idle.get_last_activity("sess") is None


async def test_metadata_only_ctime_bump_does_not_replay_transcript(tmp_path) -> None:
    """A ctime bump that leaves consumed bytes intact is not a replacement."""
    history = "".join(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"h%d"}]}}\n'
        % index
        for index in range(5)
    )
    session_file = tmp_path / "transcript.jsonl"
    session_file.write_text(history, newline="\n")

    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    state.update_session(
        TrackedSession(
            session_id="sess",
            file_path=str(session_file),
            last_byte_offset=session_file.stat().st_size,
        )
    )
    reader = TranscriptReader(state, IdleTracker())
    os.chmod(session_file, 0o600)

    messages = []
    await reader._process_session_file("sess", session_file, messages, window_id="@1")

    assert messages == []
    tracked = state.get_session("sess")
    assert tracked is not None
    assert tracked.parsed_offset == session_file.stat().st_size


async def test_append_during_read_does_not_replay_transcript(tmp_path) -> None:
    """A concurrent append mid-read must deliver the new entry, not the file."""
    history = "".join(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"h%d"}]}}\n'
        % index
        for index in range(5)
    )
    fresh = (
        '{"type":"assistant","message":{"content":[{"type":"text","text":"fresh"}]}}\n'
    )
    session_file = tmp_path / "transcript.jsonl"
    session_file.write_text(history, newline="\n")

    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    state.update_session(
        TrackedSession(
            session_id="sess",
            file_path=str(session_file),
            last_byte_offset=session_file.stat().st_size,
        )
    )
    reader = TranscriptReader(state, IdleTracker())
    original_read = reader._read_new_lines
    appended = False

    async def append_then_read(*args, **kwargs):
        nonlocal appended
        if not appended:
            with session_file.open("a") as transcript:
                transcript.write(fresh)
            appended = True
        return await original_read(*args, **kwargs)

    messages = []
    with patch.object(reader, "_read_new_lines", side_effect=append_then_read):
        await reader._process_session_file(
            "sess", session_file, messages, window_id="@1"
        )

    assert [msg.text for msg in messages] == ["fresh"]


async def test_rewrite_before_tail_marker_does_not_replay(tmp_path) -> None:
    """A rewrite outside the tail marker must not be skipped as an append."""
    history = "".join(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"h%d"}]}}\n'
        % index
        for index in range(5)
    )
    session_file = tmp_path / "transcript.jsonl"
    session_file.write_text(history, newline="\n")

    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    state.update_session(
        TrackedSession(
            session_id="sess",
            file_path=str(session_file),
            last_byte_offset=session_file.stat().st_size,
        )
    )
    reader = TranscriptReader(state, IdleTracker())
    await reader._process_session_file("sess", session_file, [], window_id="@1")
    reader._file_mtimes["sess"] = 0.0

    original_read = reader._read_new_lines
    rewritten = False

    async def rewrite_then_read(*args, **kwargs):
        nonlocal rewritten
        if not rewritten:
            session_file.write_text(
                session_file.read_text().replace('"h0"', '"changed"', 1),
                newline="\n",
            )
            rewritten = True
        return await original_read(*args, **kwargs)

    messages = []
    await reader._process_session_file("sess", session_file, messages, window_id="@1")

    assert messages == []


async def test_replaced_smaller_transcript_resumes_from_eof_no_replay(
    tmp_path,
) -> None:
    """A replaced (smaller) transcript must not be replayed as notifications.

    Regression for the 2026-08-17 flood: an adopted offset larger than the
    new file's size used to reset the offset to 0 and re-emit the whole
    history, flooding Telegram and starving every other topic.
    """
    big = '{"type":"assistant","message":{"content":[{"type":"text","text":"old"}]}}\n'
    small = (
        '{"type":"assistant","message":{"content":[{"type":"text","text":"fresh"}]}}\n'
    )
    session_file = tmp_path / "transcript.jsonl"
    session_file.write_text(small, newline="\n")

    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    # offset from the PREVIOUS (larger) transcript, carried over by adoption
    state.update_session(
        TrackedSession(
            session_id="sess-replaced",
            file_path=str(session_file),
            last_byte_offset=len(big.encode()) * 100,
        )
    )
    reader = TranscriptReader(state, IdleTracker())

    messages = []
    await reader._process_session_file(
        "sess-replaced", session_file, messages, window_id="@1"
    )

    assert messages == []  # nothing replayed from the replaced file
    tracked = state.get_session("sess-replaced")
    assert tracked is not None
    assert tracked.parsed_offset == session_file.stat().st_size


async def test_bookkeeping_lines_on_idle_session_are_not_activity(tmp_path) -> None:
    """A published artifact's ledger line lands in an idle transcript every
    hour or so; it must not paint the topic active (23.10 rename storm)."""
    old = '{"type":"assistant","message":{"content":[{"type":"text","text":"a"}]}}\n'
    session_file = tmp_path / "transcript.jsonl"
    session_file.write_text(old, newline="\n")
    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    state.update_session(
        TrackedSession(
            session_id="sess",
            file_path=str(session_file),
            last_byte_offset=len(old.encode()),
        )
    )
    idle = IdleTracker()
    reader = TranscriptReader(state, idle)
    await reader._process_session_file("sess", session_file, [], window_id="@1")
    with session_file.open("a") as transcript:
        transcript.write(
            '{"type":"artifact-autoreact-ledger","v":1,"sessionId":"sess"}\n'
        )

    await reader._process_session_file("sess", session_file, [], window_id="@1")
    assert idle.get_last_activity("sess") is None

    with session_file.open("a") as transcript:
        transcript.write(
            '{"type":"user","message":{"content":[{"type":"text","text":"b"}]}}\n'
        )
    await reader._process_session_file("sess", session_file, [], window_id="@1")
    assert idle.get_last_activity("sess") is not None


def _assistant(blocks, stop=None) -> str:
    import json

    return (
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": blocks, "stop_reason": stop},
            },
            ensure_ascii=False,
        )
        + "\n"
    )


async def test_turn_end_marks_only_the_closing_text(tmp_path) -> None:
    """CCGRAM-HOTFIX:webui-extras — the reply whose API message ended the turn
    carries ``turn_end`` ("notify when done" rings on it only)."""
    session_file = tmp_path / "transcript.jsonl"
    session_file.write_text(
        _assistant([{"type": "text", "text": "смотрю"}], "tool_use")
        + _assistant(
            [{"type": "tool_use", "id": "T1", "name": "Bash", "input": {}}],
            "tool_use",
        )
        + _assistant([{"type": "thinking", "thinking": "ok" * 20}], "end_turn")
        + _assistant([{"type": "text", "text": "готово"}], "end_turn"),
        encoding="utf-8",
    )
    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    state.update_session(
        TrackedSession("sess", str(session_file), last_byte_offset=0)
    )
    reader = TranscriptReader(state, IdleTracker())

    messages = []
    await reader._process_session_file("sess", session_file, messages, window_id="@1")

    assert [(m.content_type, m.text) for m in messages if m.turn_end] == [
        ("text", "готово")
    ]


async def test_non_utf8_line_is_decoded_instead_of_stalling(tmp_path) -> None:
    """CCGRAM-HOTFIX:transcript-decode-guard — a stray non-UTF-8 byte inside a
    JSON string is replaced, so the line (and everything after it) relays."""
    bad = b'{"type":"assistant","message":{"content":[{"type":"text","text":"a\xff"}]}}\n'
    good = _assistant([{"type": "text", "text": "b"}]).encode()
    session_file = tmp_path / "transcript.jsonl"
    session_file.write_bytes(bad + good)
    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    state.update_session(
        TrackedSession("sess", str(session_file), last_byte_offset=0)
    )
    reader = TranscriptReader(state, IdleTracker())

    messages = []
    await reader._process_session_file("sess", session_file, messages, window_id="@1")

    assert [m.text for m in messages] == ["a�", "b"]
    tracked = state.get_session("sess")
    assert tracked is not None
    assert tracked.parsed_offset == session_file.stat().st_size


async def test_grok_rewrite_relays_only_new_entries(tmp_path) -> None:
    """The fork's grok provider rewrites chat_history.jsonl in place; the
    whole-file read diffs by entry index, so earlier lines rewritten with
    different bytes are neither replayed nor mistaken for a replacement."""
    import json

    from ccgram.providers.grok import GrokProvider

    def line(entry: dict) -> str:
        return json.dumps(entry, ensure_ascii=False) + "\n"

    first = [
        {"type": "user", "content": "<user_query>\nпривет\n</user_query>"},
        {"type": "reasoning", "summary": [], "status": "in_progress"},
    ]
    session_file = tmp_path / "chat_history.jsonl"
    session_file.write_text("".join(line(e) for e in first), encoding="utf-8")
    state = MonitorState(state_file=tmp_path / "monitor_state.json")
    reader = TranscriptReader(state, IdleTracker())
    provider = GrokProvider()

    with patch(
        "ccgram.transcript_reader._resolve_provider_for_file", return_value=provider
    ):
        messages = []
        await reader._process_session_file(
            "g1", session_file, messages, window_id="@1"
        )
        assert messages == []  # existing entries are history

        first[1]["status"] = "completed"  # earlier line rewritten, new bytes
        rewritten = first + [{"type": "assistant", "content": "Готово."}]
        session_file.write_text(
            "".join(line(e) for e in rewritten), encoding="utf-8"
        )
        await reader._process_session_file(
            "g1", session_file, messages, window_id="@1"
        )

    assert [(m.role, m.text) for m in messages] == [("assistant", "Готово.")]
    tracked = state.get_session("g1")
    assert tracked is not None
    assert tracked.parsed_offset == 3
