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
