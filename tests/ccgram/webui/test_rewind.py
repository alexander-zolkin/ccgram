import json

from ccgram.webui import rewind, transcript


def test_relaunch_keeps_flags_and_resumes():
    argv = [
        "claude",
        "--dangerously-skip-permissions",
        "--effort",
        "xhigh",
        "--model",
        "claude-opus-5-5[1m]",
        "--resume",
        "old",
        "--continue",
        "hello there",
    ]
    assert rewind.relaunch_argv(argv, "sid") == [
        "claude",
        "--dangerously-skip-permissions",
        "--effort",
        "xhigh",
        "--model",
        "claude-opus-5-5[1m]",
        "--resume",
        "sid",
    ]
    assert rewind.relaunch_argv(["claude", "--model=haiku"], "s") == [
        "claude",
        "--model=haiku",
        "--resume",
        "s",
    ]


def test_mark_and_hidden_range_hide_the_abandoned_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(rewind, "_store_path", lambda: tmp_path / "rewinds.json")
    rewind._cache.update(mtime=None, data={})
    p = tmp_path / "s.jsonl"
    lines = [
        {
            "type": "user",
            "uuid": "u1",
            "parentUuid": None,
            "message": {"content": "раз"},
        },
        {
            "type": "assistant",
            "uuid": "a1",
            "parentUuid": "u1",
            "message": {"content": [{"type": "text", "text": "ок1"}]},
        },
        {
            "type": "user",
            "uuid": "u2",
            "parentUuid": "a1",
            "message": {"content": "два"},
        },
        {
            "type": "assistant",
            "uuid": "a2",
            "parentUuid": "u2",
            "message": {"content": [{"type": "text", "text": "ок2"}]},
        },
    ]
    p.write_text("".join(json.dumps(x) + "\n" for x in lines))
    at, entry = rewind.find_prompt(p, "u2")
    assert entry["parentUuid"] == "a1" and rewind.prompt_text(entry) == "два"
    end = rewind.mark(p, "s", entry["parentUuid"])
    rewind._remember(p, at, end)
    with p.open("a") as fh:
        fh.write(
            json.dumps(
                {
                    "type": "user",
                    "uuid": "u3",
                    "parentUuid": "a1",
                    "message": {"content": "три"},
                }
            )
            + "\n"
        )
    hidden = rewind.hidden_ranges(p)
    texts = [it["text"] for it in transcript.read_tail(p, 50, hidden=hidden)["items"]]
    assert texts == ["раз", "ок1", "три"]
    assert [
        it["text"] for it in transcript.read_after(p, 0, hidden=hidden)["items"]
    ] == texts
    marker = json.loads(p.read_text().splitlines()[4])
    assert marker == {
        "type": "last-prompt",
        "leafUuid": "a1",
        "explicit": True,
        "rewound": True,
        "sessionId": "s",
    }
    assert rewind.find_prompt(p, "u2") is None  # rewound away: not offered again


def test_only_a_person_s_prompt_rewinds():
    tool = {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": "t"}]},
    }
    for entry in (tool, {"type": "assistant"}, {"type": "user", "isMeta": True}):
        try:
            rewind._check_prompt(entry)
        except rewind.RewindError:
            continue
        raise AssertionError(entry)
