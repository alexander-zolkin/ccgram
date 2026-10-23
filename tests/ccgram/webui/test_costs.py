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
