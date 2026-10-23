import json

from ccgram.webui import transcript


def _line(entry):
    return json.dumps(entry, ensure_ascii=False) + "\n"


def _user(text, uuid):
    return {"type": "user", "uuid": uuid, "message": {"content": text}}


def _assistant(blocks, uuid):
    return {"type": "assistant", "uuid": uuid, "message": {"content": blocks}}


def _tool_result(tool_id, text, uuid, tur=None, is_error=False):
    entry = {
        "type": "user",
        "uuid": uuid,
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": text,
                    "is_error": is_error,
                }
            ]
        },
    }
    if tur is not None:
        entry["toolUseResult"] = tur
    return entry


def _write(path, entries):
    path.write_text("".join(_line(e) for e in entries), encoding="utf-8")


def test_parse_shapes_and_merge(tmp_path):
    p = tmp_path / "t.jsonl"
    _write(
        p,
        [
            _user("привет", "u1"),
            _user("<command-name>/clear</command-name>", "u2"),
            _user("Continue from where you left off.", "u3"),
            {"type": "user", "isMeta": True, "uuid": "u4", "message": {"content": "x"}},
            _assistant(
                [
                    {"type": "thinking", "thinking": ""},
                    {"type": "text", "text": "Смотрю"},
                    {
                        "type": "tool_use",
                        "id": "T1",
                        "name": "Bash",
                        "input": {"command": "ls", "description": "list"},
                    },
                    {
                        "type": "tool_use",
                        "id": "T2",
                        "name": "Edit",
                        "input": {
                            "file_path": "/a",
                            "old_string": "x",
                            "new_string": "y",
                        },
                    },
                ],
                "a1",
            ),
            _tool_result("T1", "out", "r1", {"stdout": "a\nb", "stderr": ""}),
            _tool_result(
                "T2",
                "ok",
                "r2",
                {
                    "structuredPatch": [
                        {"oldStart": 1, "newStart": 1, "lines": ["-x", "+y"]}
                    ]
                },
            ),
            {
                "type": "assistant",
                "isSidechain": True,
                "uuid": "s1",
                "message": {"content": [{"type": "text", "text": "sub"}]},
            },
            {"type": "system", "uuid": "sys"},
        ],
    )
    items = transcript.read_tail(p, 50)["items"]
    kinds = [i["kind"] for i in items]
    assert kinds == ["user", "command", "thinking", "assistant", "tool", "tool"]
    assert items[1]["text"] == "/clear"
    bash, edit = items[4], items[5]
    assert bash["result"]["stdout"] == "a\nb"
    assert "old_string" not in edit["input"]
    assert edit["result"]["patch"][0]["lines"] == ["-x", "+y"]


def test_paste_wrappers_are_not_shown():
    wrapped = '<pasted_content id="caa1">\nДело:\n> текст\n</pasted_content id="caa1">'
    (item,) = transcript.parse_entry(_user(wrapped, "u1"))
    assert item["text"] == "Дело:\n> текст"
    blocks = [{"type": "text", "text": "смотри\n" + wrapped + "\nчто это?"}]
    (item,) = transcript.parse_entry(_user(blocks, "u2"))
    assert item["text"] == "смотри\nДело:\n> текст\nчто это?"


def test_message_typed_mid_turn_shows_where_the_model_got_it():
    def queued(prompt, mode, kind):
        return {
            "type": "attachment",
            "uuid": "q1",
            "timestamp": "2026-10-22T23:41:48.241Z",
            "attachment": {
                "type": "queued_command",
                "prompt": prompt,
                "commandMode": mode,
                "origin": {"kind": kind},
            },
        }

    (item,) = transcript.parse_entry(queued("а если один вопрос?", "prompt", "human"))
    assert item["kind"] == "user" and item["text"] == "а если один вопрос?"
    assert item["queued"] and item["ts"] == "2026-10-22T23:41:48.241Z"
    note = "<task-notification>\n<task-id>x</task-id>\n</task-notification>"
    assert transcript.parse_entry(queued(note, "task-notification", "task")) == []
    other = {"type": "attachment", "attachment": {"type": "hook_success"}}
    assert transcript.parse_entry(other) == []


def test_message_from_another_session_is_not_the_persons():
    body = "Heads-up: I'm editing app.js too."
    wrapped = (
        '<cross-session-message from="uds:/tmp/cc-socks/1.sock" '
        f'from-name="workspace-18" from-mode="bypass">\n{body}\n'
        "</cross-session-message>"
    )
    peer = {"kind": "peer", "from": "uds:/tmp/cc-socks/1.sock", "name": "workspace-18"}
    queued = {
        "type": "attachment",
        "uuid": "p1",
        "timestamp": "2026-10-23T01:26:58.132Z",
        "attachment": {
            "type": "queued_command",
            "prompt": wrapped,
            "commandMode": "prompt",
            "origin": {**peer, "body": body},
            "isMeta": True,
        },
    }
    (item,) = transcript.parse_entry(queued)
    assert item["kind"] == "peer" and item["from"] == "workspace-18"
    assert item["text"] == body and not item["handback"]

    # A subagent's report delivered to an idle session: a meta ``user`` entry.
    report = "Done: 4 workflows off."
    idle = {
        "type": "user",
        "uuid": "p2",
        "isMeta": True,
        "message": {"content": '<agent-message from="a1">\n[Subagent hand-back] …'},
        "origin": {
            "kind": "peer",
            "name": "n8n-workflow-editor",
            "body": "[Subagent hand-back] …",
            "handback": True,
            "handbackReportText": report,
        },
    }
    (item,) = transcript.parse_entry(idle)
    assert item["kind"] == "peer" and item["handback"] and item["text"] == report
    assert transcript._is_prompt(idle, [item])

    boss = {
        **queued,
        "attachment": {**queued["attachment"], "origin": {"kind": "coordinator"}},
    }
    assert transcript.parse_entry(boss) == []


def test_after_returns_late_result_standalone(tmp_path):
    p = tmp_path / "t.jsonl"
    call = _assistant(
        [
            {
                "type": "tool_use",
                "id": "T1",
                "name": "Read",
                "input": {"file_path": "/f"},
            }
        ],
        "a1",
    )
    _write(p, [_user("hi", "u1"), call])
    first = transcript.read_tail(p, 50)
    assert first["items"][-1]["kind"] == "tool"
    assert "result" not in first["items"][-1]
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(_line(_tool_result("T1", "content", "r1")))
        fh.write('{"type": "user", "partial')  # line still being written
    nxt = transcript.read_after(p, first["end"])
    assert [i["kind"] for i in nxt["items"]] == ["tool_result"]
    assert nxt["items"][0]["id"] == "T1"
    assert transcript.read_after(p, nxt["end"])["items"] == []


def test_tail_pages_backwards_without_gaps(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript, "CHUNK", 64)  # force many small chunks
    p = tmp_path / "t.jsonl"
    _write(p, [_user(f"m{i}", f"u{i}") for i in range(40)])
    seen, before = [], None
    while True:
        page = transcript.read_tail(p, 7, before)
        seen = [i["text"] for i in page["items"]] + seen
        if page["start"] == 0:
            break
        before = page["start"]
    # Pages may overlap at the edges (the client de-duplicates), never skip.
    assert sorted(set(seen), key=lambda s: int(s[1:])) == [f"m{i}" for i in range(40)]


def test_tail_ignores_unfinished_last_line(tmp_path):
    p = tmp_path / "t.jsonl"
    _write(p, [_user("done", "u1")])
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"type": "user", "mess')
    page = transcript.read_tail(p, 10)
    assert [i["text"] for i in page["items"]] == ["done"]
    assert page["end"] == len(_line(_user("done", "u1")).encode())


def test_clip_large_fields(tmp_path):
    p = tmp_path / "t.jsonl"
    big = "x" * 50_000
    call = _assistant(
        [{"type": "tool_use", "id": "T1", "name": "Bash", "input": {"command": "cat"}}],
        "a1",
    )
    _write(p, [call, _tool_result("T1", big, "r1", {"stdout": big, "stderr": ""})])
    item = transcript.read_tail(p, 10)["items"][0]
    assert len(item["result"]["stdout"]) < 7000
    assert "chars)" in item["result"]["stdout"]


def test_shell_runs_summaries_and_command_output(tmp_path):
    p = tmp_path / "t.jsonl"
    _write(
        p,
        [
            _user("<bash-input>ls -la</bash-input>", "b1"),
            _user(
                "<bash-stdout>a\nb</bash-stdout><bash-stderr>\x1b[31moops\x1b[0m</bash-stderr>",
                "b2",
            ),
            _user("<local-command-stdout>Set model</local-command-stdout>", "c1"),
            _user("<local-command-stdout></local-command-stdout>", "c2"),
            {
                "type": "user",
                "isCompactSummary": True,
                "uuid": "s1",
                "message": {"content": "Summary: did things"},
            },
        ],
    )
    items = transcript.read_tail(p, 50)["items"]
    assert [i["kind"] for i in items] == [
        "bash",
        "bash_result",
        "command_result",
        "summary",
    ]
    assert items[0]["command"] == "ls -la"
    assert items[1]["stdout"] == "a\nb" and items[1]["stderr"] == "oops"
    assert items[3]["text"] == "Summary: did things"


def test_last_usage_and_search(tmp_path):
    p = tmp_path / "t.jsonl"
    usage = {"input_tokens": 10, "cache_read_input_tokens": 1000, "output_tokens": 5}
    _write(
        p,
        [
            _user("Найди Автоплатёж в коде", "u1"),
            {
                "type": "assistant",
                "uuid": "a1",
                "message": {
                    "model": "claude-x",
                    "usage": usage,
                    "content": [{"type": "text", "text": "автоплатёж нашла"}],
                },
            },
        ],
    )
    assert transcript.last_usage(p) == {"model": "claude-x", "context": 1015}
    hits = transcript.search(p, "автоплатёж")
    assert [h["role"] for h in hits] == ["user", "assistant"]
    assert transcript.search(p, "нет такого") == []
    assert transcript.last_usage(tmp_path / "missing.jsonl") is None


def _usage(blocks, uuid, mid, out):
    entry = _assistant(blocks, uuid)
    entry["message"].update(id=mid, usage={"input_tokens": 2, "output_tokens": out})
    return entry


def _turn_end(ms, uuid):
    return {
        "type": "system",
        "subtype": "turn_duration",
        "durationMs": ms,
        "uuid": uuid,
    }


def test_turn_stats_sum_tokens_per_message(tmp_path):
    p = tmp_path / "t.jsonl"
    _write(
        p,
        [
            _user("первый", "u1"),
            _usage([{"type": "thinking", "thinking": ""}], "a1", "m1", 300),
            _usage([{"type": "text", "text": "ответ"}], "a2", "m1", 300),
            _tool_result("T1", "ok", "r1"),
            _usage([{"type": "text", "text": "ещё"}], "a3", "m2", 50),
            _turn_end(19583, "d1"),
            _user("второй", "u2"),
            _usage([{"type": "text", "text": "коротко"}], "a4", "m3", 7),
            _turn_end(1200, "d2"),
        ],
    )
    turns = [it for it in transcript.read_tail(p, 50)["items"] if it["kind"] == "turn"]
    assert [(t["ms"], t["out"]) for t in turns] == [(19583, 350), (1200, 7)]


def test_turn_end_in_a_later_poll_reads_back(tmp_path):
    p = tmp_path / "t.jsonl"
    head = [
        _user("привет", "u1"),
        _usage([{"type": "text", "text": "a"}], "a1", "m1", 120),
    ]
    _write(p, head)
    first = transcript.read_after(p, 0)
    assert transcript.current_turn(p)["out"] == 120
    assert transcript.current_turn(p)["start"] is None  # no timestamp in fixture
    with p.open("a", encoding="utf-8") as fh:
        fh.write(_line(_usage([{"type": "text", "text": "b"}], "a2", "m2", 30)))
        fh.write(_line(_turn_end(5000, "d1")))
    later = transcript.read_after(p, first["end"])
    (turn,) = [it for it in later["items"] if it["kind"] == "turn"]
    assert turn["out"] == 150
    assert transcript.current_turn(p) == {"start": None, "out": 0}


def test_current_turn_reports_prompt_time(tmp_path):
    p = tmp_path / "t.jsonl"
    prompt = _user("сделай", "u1")
    prompt["timestamp"] = "2026-10-22T18:44:00.658Z"
    _write(p, [_turn_end(10, "d0"), prompt, _usage([], "a1", "m1", 42)])
    assert transcript.current_turn(p) == {
        "start": "2026-10-22T18:44:00.658Z",
        "out": 42,
    }


def test_interrupt_ends_the_turn_without_turn_duration(tmp_path):
    p = tmp_path / "t.jsonl"
    prompt = _user("сделай", "u1")
    prompt["timestamp"] = "2026-10-22T18:44:00.658Z"
    stop = _user([{"type": "text", "text": "[Request interrupted by user]"}], "u2")
    _write(p, [prompt, _usage([], "a1", "m1", 42), stop])
    assert transcript.current_turn(p) == {"start": None, "out": 0}


def test_daily_stats_buckets_by_local_day(tmp_path):
    from datetime import datetime, timedelta

    p = tmp_path / "t.jsonl"
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    def at(days, hour):
        moment = midnight - timedelta(days=days) + timedelta(hours=hour)
        return moment.astimezone().isoformat()

    entries = []
    for days, out, mid in (
        (9, 500, "old"),
        (2, 100, "m1"),
        (2, 100, "m1"),
        (1, 40, "m2"),
        (0, 7, "m3"),
    ):
        e = _usage([{"type": "text", "text": "x"}], f"a{days}{mid}", mid, out)
        e["timestamp"] = at(days, 10)
        entries.append(e)
    end = _turn_end(3000, "d1")
    end["timestamp"] = at(1, 11)
    entries.append(end)
    _write(p, entries)
    got = transcript.daily_stats(
        p, (midnight - timedelta(days=6)).timestamp(), midnight.timestamp()
    )

    def day(n):
        return (midnight - timedelta(days=n)).date().isoformat()

    assert got == {day(2): {"out": 100, "turns": 0}, day(1): {"out": 40, "turns": 1}}


def test_last_effort_reads_only_what_was_appended(tmp_path):
    p = tmp_path / "t.jsonl"
    said = "<local-command-stdout>Set effort level to {} (saved)</local-command-stdout>"
    quoted = _tool_result("t1", said.format("low"), "r1")  # inside tool output
    _write(p, [_user("hi", "u1"), quoted])
    assert transcript.last_effort(p) is None
    with p.open("a", encoding="utf-8") as fh:
        fh.write(_line(_user(said.format("high"), "u2")))
        fh.write(json.dumps(_user(said.format("max"), "u3"))[:40])  # half a line
    assert transcript.last_effort(p) == "high"
    blocks = [{"type": "text", "text": said.format("medium")}]
    with p.open("a", encoding="utf-8") as fh:
        fh.write("\n" + _line(_user(blocks, "u4")))
    assert transcript.last_effort(p) == "medium"
    assert transcript.last_effort(tmp_path / "missing.jsonl") is None
