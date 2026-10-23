import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ccgram.webui import costs

NOW = datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _say(mid, model, usage, **extra):
    return {
        "type": "assistant",
        "timestamp": NOW,
        "message": {"id": mid, "model": model, "usage": usage},
        **extra,
    }


def _write(path: Path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))


def test_cost_matches_claude_code_session_total():
    # Claude Code's /usage: opus-5-5 13.4k in, 75.0k out, 5.7m cache read,
    # 253.5k cache write (1 h) → $4.72
    tally = costs.Tally()
    usage = {
        "input_tokens": 13_400,
        "output_tokens": 75_000,
        "cache_read_input_tokens": 5_700_000,
        "cache_creation": {"ephemeral_1h_input_tokens": 253_500},
    }
    assert tally.add("claude-opus-5-5[1m]", usage) == pytest.approx(4.7216)
    assert costs.price("claude-haiku-4-5-20251001") == costs.PRICES["claude-haiku-4"]
    assert costs.price("gpt-5") is None
    fast = costs.Tally().add("claude-opus-5-5", {"output_tokens": 1e6, "speed": "fast"})
    assert fast == pytest.approx(40.0)


def test_chat_counts_each_message_once_and_its_subagents(tmp_path):
    main = tmp_path / "proj" / "s1.jsonl"
    usage = {"input_tokens": 1_000_000, "output_tokens": 0}
    patch = {"structuredPatch": [{"lines": [" a", "-b", "+c", "+d"]}]}
    _write(
        main,
        [
            _say("m1", "claude-opus-5-5", usage),  # two blocks, one message
            _say("m1", "claude-opus-5-5", usage),
            {"type": "user", "timestamp": NOW, "toolUseResult": patch},
            {
                "type": "system",
                "subtype": "turn_duration",
                "durationMs": 61000,
                "timestamp": NOW,
            },
            _say("x", "<synthetic>", usage),
        ],
    )
    _write(
        tmp_path / "proj" / "s1" / "subagents" / "agent-1.jsonl",
        [_say("m2", "claude-haiku-5-5", usage)],
    )
    got = costs.chat_stats(main)
    assert got["cost"] == pytest.approx(4.1)  # $4 opus + $0.10 haiku
    assert [m["model"] for m in got["models"]] == [
        "claude-opus-5-5",
        "claude-haiku-5-5",
    ]
    assert (got["turns"], got["busy_s"], got["subagents"]) == (1, 61, 1)
    assert (got["lines_added"], got["lines_removed"]) == (2, 1)


def test_period_skips_the_private_root_and_names_chats(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    usage = {"input_tokens": 0, "output_tokens": 100_000}  # $2 on opus-5-5
    _write(
        tmp_path / "projects" / "-work" / "s1.jsonl",
        [_say("a", "claude-opus-5-5", usage)],
    )
    _write(
        tmp_path / "projects" / "-work" / "s2.jsonl",
        [_say("b", "claude-opus-5-5", usage)],
    )
    _write(
        tmp_path / "projects" / "-home-me-private-x" / "p.jsonl",
        [_say("c", "claude-opus-5-5", usage)],
    )
    got = costs.period_stats(Path("/home/me/private"), {"s1": "Финансы"})
    assert got["today"]["cost"] == pytest.approx(4.0)  # the private $2 is not here
    assert got["month"]["sessions"] == 2 and len(got["days"]) == costs.PERIOD_DAYS
    assert got["chats"] == [{"name": "Финансы", "cost": 2.0}]
    assert got["chats_rest"] == pytest.approx(2.0)


def test_activity_streaks_busiest_day_and_heatmap(tmp_path, monkeypatch):
    from datetime import date, timedelta

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    def at(days_ago, mid, out):
        when = datetime.combine(
            date.today() - timedelta(days=days_ago), datetime.min.time()
        )
        when = when.replace(hour=12).astimezone(UTC).isoformat()
        return {
            **_say(mid, "claude-opus-5-5", {"output_tokens": out}),
            "timestamp": when,
        }

    _write(
        tmp_path / "projects" / "-w" / "s1.jsonl",
        [at(5, "a", 10), at(2, "b", 10), at(1, "c", 500), at(0, "d", 10)],
    )
    got = costs.period_stats(Path("/nowhere/private"), {})
    every = got["all"]
    assert (every["days"], every["active_days"]) == (6, 4)
    assert (every["longest_streak"], every["current_streak"]) == (3, 3)
    assert every["busiest"]["out"] == 500
    assert every["favorite"] == "claude-opus-5-5"
    assert got["week"]["output"] == 530 and got["today"]["output"] == 10
    first = date.fromisoformat(got["heatmap"][0]["date"])
    assert first.weekday() == 0 and len(got["heatmap"]) >= 26 * 7
    assert got["heatmap"][-1]["date"] == date.today().isoformat()


def test_life_since_birthday_counts_openclaw_replies_once(tmp_path, monkeypatch):
    from datetime import date, timedelta

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    usage = {"input": 0, "output": 1_000_000, "cacheRead": 0, "cacheWrite": 0}

    def reply(rid, provider="anthropic", model="claude-opus-4-6"):
        msg = {"role": "assistant", "provider": provider, "model": model}
        return {
            "type": "message",
            "id": rid,
            "timestamp": NOW,
            "message": {**msg, "responseId": rid, "usage": usage},
        }

    hist = tmp_path / "openclaw"
    _write(
        hist / "s1.jsonl.reset.2026-05-01",
        [reply("r1"), reply("r2", "openclaw", "delivery-mirror")],
    )
    _write(hist / "s1.checkpoint.abc.jsonl", [reply("r1")])  # a copy of the same reply
    born = date.today() - timedelta(days=40)
    got = costs.period_stats(Path("/nowhere/private"), {}, born=born, history=[hist])
    every = got["all"]
    assert (every["since"], every["days"], got["born"]) == (
        born.isoformat(),
        41,
        born.isoformat(),
    )
    assert every["cost"] == pytest.approx(25.0)  # Opus 4.6: $25 per 1M out, once
    assert every["sessions"] == 1 and every["favorite"] == "claude-opus-4-6"
    assert got["heatmap"][0]["date"] <= born.isoformat()


def test_archived_transcripts_fill_the_past_once_and_stay_cached(tmp_path, monkeypatch):
    from datetime import date, timedelta

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    costs.reset_for_testing()
    old = datetime.combine(date.today() - timedelta(days=60), datetime.min.time())
    old = old.replace(hour=12).astimezone(UTC).isoformat()
    usage = {"output_tokens": 50_000}  # $1 on opus-5-5
    now_reply = _say("a", "claude-opus-5-5", usage)
    _write(tmp_path / "projects" / "-w" / "s1.jsonl", [now_reply])
    arch = tmp_path / "backup" / "claude-projects"
    _write(arch / "-w" / "s1.jsonl", [now_reply])  # the same reply, backed up
    _write(
        arch / "-w" / "s0.jsonl",
        [{**_say("b", "claude-opus-5-5", usage), "timestamp": old}],
    )
    _write(arch / "-home-me-private" / "p.jsonl", [_say("c", "claude-opus-5-5", usage)])
    run = lambda: costs.period_stats(Path("/home/me/private"), {}, archive=[arch])  # noqa: E731
    got = run()
    assert got["all"]["cost"] == pytest.approx(
        2.0
    )  # a + b; the copy and private skipped
    assert got["all"]["sessions"] == 2 and got["all"]["days"] == 61
    cached = costs._archived
    assert run()["all"]["cost"] == pytest.approx(2.0) and costs._archived is cached
    costs.reset_for_testing()


def test_store_keeps_finished_days_and_rereads_only_today(tmp_path, monkeypatch):
    from datetime import date, timedelta

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    costs.reset_for_testing()
    past = datetime.combine(date.today() - timedelta(days=3), datetime.min.time())
    past = past.replace(hour=12).astimezone(UTC).isoformat()
    usage = {"output_tokens": 50_000}  # $1 on opus-5-5
    old = tmp_path / "projects" / "-w" / "old.jsonl"
    now = tmp_path / "projects" / "-w" / "now.jsonl"
    _write(old, [{**_say("p", "claude-opus-5-5", usage), "timestamp": past}])
    _write(now, [_say("n1", "claude-opus-5-5", usage)])
    store = tmp_path / "stats_days.json"
    run = lambda: costs.period_stats(Path("/nowhere/private"), {}, store=store)  # noqa: E731
    assert run()["all"]["cost"] == pytest.approx(2.0)
    saved = json.loads(store.read_text())
    assert saved["closed_until"] == date.today().isoformat()
    assert list(saved["days"]) == [(date.today() - timedelta(days=3)).isoformat()]

    old.unlink()  # cleaned up — the finished day stays in the store
    with now.open("a") as fh:
        fh.write(json.dumps(_say("n2", "claude-opus-5-5", usage)) + "\n")
    got = run()
    assert got["all"]["cost"] == pytest.approx(3.0) and got["today"][
        "cost"
    ] == pytest.approx(2.0)


def test_chat_stats_read_only_what_was_appended(tmp_path, monkeypatch):
    costs.reset_for_testing()
    path = tmp_path / "s.jsonl"
    usage = {"output_tokens": 50_000}
    _write(path, [_say("a", "claude-opus-5-5", usage)])
    assert costs.chat_stats(path)["cost"] == pytest.approx(1.0)
    with path.open("a") as fh:
        fh.write(json.dumps(_say("b", "claude-opus-5-5", usage)) + "\n")
        fh.write('{"half a line')
    assert costs.chat_stats(path)["cost"] == pytest.approx(2.0)
    assert costs._chat_files[str(path)].offset < path.stat().st_size  # the tail waits


def test_other_providers_are_priced_at_their_list_prices():
    # OpenClaw's own bill for this gpt-5.4 reply: $0.044097
    parts = (14_766, 402, 4_608, 0, 0)
    assert costs.Tally().add_parts("gpt-5.4", parts) == pytest.approx(0.044097)
    assert costs.price("gpt-5.4-mini") == costs.PRICES["gpt-5.4-mini"]
    assert costs.price("grok-4.20-0309-reasoning") == costs.PRICES["grok-4.20"]
    assert costs.price("meta-llama/llama-3.3-70b-instruct:free") == costs.FREE
    assert costs.price("google/gemini-2.5-pro") == costs.PRICES["gemini-2.5-pro"]
    assert costs.price("gemini-3.1-flash") is None
    who = {m: costs.provider(m) for m in ("claude-opus-5-5[1m]", "gpt-5.5", "grok-4-1-fast", "gemini-3-flash-preview", "coder-model", "qwen/qwen3-235b:free", "mystery")}  # fmt: skip
    assert list(who.values()) == ["anthropic", "openai", "xai", "google", "alibaba", "alibaba", "other"]  # fmt: skip


def _rollout(cwd, *events, model="gpt-5.4"):
    def count(stamp, total, last):
        keys = ("input_tokens", "cached_input_tokens", "output_tokens")
        info = {
            "total_token_usage": dict(zip(keys, total, strict=True)),
            "last_token_usage": dict(zip(keys, last, strict=True)),
        }
        return {
            "timestamp": stamp,
            "type": "event_msg",
            "payload": {"type": "token_count", "info": info},
        }

    return [
        {"timestamp": NOW, "type": "session_meta", "payload": {"cwd": cwd}},
        {"timestamp": NOW, "type": "turn_context", "payload": {"model": model}},
        *(count(*e) for e in events),
    ]


def test_codex_rollout_counts_each_request_once(tmp_path):
    t = lambda s: f"2026-10-20T10:00:{s:02d}.000Z"  # noqa: E731
    events = [
        (t(1), (1_000_000, 0, 0), (1_000_000, 0, 0)),  # first: its own request
        (t(2), (1_000_000, 0, 0), (1_000_000, 0, 0)),  # the same total again
        (t(3), (3_000_000, 1_000_000, 100_000), (2_000_000, 1_000_000, 100_000)),
        (t(4), (500_000, 0, 0), (500_000, 0, 0)),  # the total went back
    ]
    live, backup = tmp_path / "live" / "2026" / "10", tmp_path / "backup"
    _write(live / "rollout-a.jsonl", _rollout("/w", *events))
    _write(backup / "rollout-a.jsonl", _rollout("/w", *events[:1]))  # a shorter copy
    _write(live / "rollout-p.jsonl", _rollout("/home/me/private/x", *events))
    files = costs._codex_files([tmp_path / "live", backup])
    assert [f.parent.name for f in files] == ["10", "10"]  # the longer copy
    rows = costs._codex_rows(live / "rollout-a.jsonl", "/home/me/private")
    assert [r[3] for r in rows] == [
        (1_000_000, 0, 0, 0, 0),
        (1_000_000, 100_000, 1_000_000, 0, 0),  # cached input split off
        (500_000, 0, 0, 0, 0),
    ]
    assert {r[2] for r in rows} == {"gpt-5.4"} and rows[0][1] == "2026-10-20"
    assert costs._codex_rows(live / "rollout-p.jsonl", "/home/me/private") == []


def test_spend_adds_every_machine_and_reads_a_file_once(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    costs.reset_for_testing()
    out = {"output_tokens": 50_000}  # $1 on opus-5-5
    _write(
        tmp_path / "projects" / "-w" / "s1.jsonl", [_say("a", "claude-opus-5-5", out)]
    )
    oc = {"input": 0, "output": 80_000, "cacheRead": 0, "cacheWrite": 0}  # $2 on 4.6
    reply = {"role": "assistant", "provider": "anthropic", "model": "claude-opus-4-6"}
    reply = {**reply, "responseId": "r", "usage": oc}
    _write(
        tmp_path / "openclaw" / "s.jsonl",
        [{"type": "message", "id": "r", "timestamp": NOW, "message": reply}],
    )
    period = costs._build_period(
        Path("/nowhere/private"),
        born=None,
        history=[tmp_path / "openclaw"],
        archive=[],
        store=None,
    )
    laptop = tmp_path / "laptop"
    twice = _say("b", "claude-opus-5-5", out)
    _write(laptop / "claude" / "-p" / "s2.jsonl", [twice])
    _write(laptop / "claude" / "-p" / "s3.jsonl", [twice])  # a fork repeats it
    _write(
        laptop / "codex" / "rollout-x.jsonl",
        _rollout("/w", (NOW, (0, 0, 100_000), (0, 0, 100_000))),  # $1.5 on gpt-5.4
    )
    reads = []
    real = costs._claude_rows
    monkeypatch.setattr(costs, "_claude_rows", lambda f: reads.append(f) or real(f))
    machines = [("Ноутбук", [laptop / "claude"], [laptop / "codex"])]

    def run():
        return costs.spend(
            period, Path("/nowhere/private"), here="Сервер", machines=machines
        )

    got = run()
    assert got["cost"] == pytest.approx(5.5)
    where = [(m["name"], m["cost"], [(t["name"], t["cost"]) for t in m["tools"]]) for m in got["machines"]]  # fmt: skip
    assert where == [
        ("Сервер", 3.0, [("OpenClaw", 2.0), ("Claude Code", 1.0)]),
        ("Ноутбук", 2.5, [("Codex", 1.5), ("Claude Code", 1.0)]),  # b once
    ]
    assert [(p["provider"], p["cost"]) for p in got["providers"]] == [
        ("anthropic", 4.0),
        ("openai", 1.5),
    ]
    month = got["months"][-1]
    assert month["providers"] == {"anthropic": 4.0, "openai": 1.5}
    assert {m["model"]: m["provider"] for m in got["models"]}["gpt-5.4"] == "openai"
    assert len(reads) == 2
    assert run()["cost"] == pytest.approx(5.5) and len(reads) == 2  # unchanged files
    costs.reset_for_testing()
