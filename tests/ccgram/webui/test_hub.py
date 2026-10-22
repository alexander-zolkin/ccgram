import json
import time
from pathlib import Path
from datetime import date, timedelta

import pytest

from ccgram.webui import hub
from ccgram.webui.service import WebUIError


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A CCGRAM_DIR with hub.toml pointing at a small memory store."""
    monkeypatch.setenv("CCGRAM_DIR", str(tmp_path))
    mem = tmp_path / "memory"
    (mem / "n100").mkdir(parents=True)
    (mem / "_archive").mkdir()
    (mem / "n100" / "index.md").write_text(
        "- [Laptop SSH](reference_laptop.md) — how to reach it\n", encoding="utf-8"
    )
    (mem / "n100" / "reference_laptop.md").write_text(
        "---\nname: laptop-ssh\ndescription: SSH to the Fedora laptop\n---\nUse `ssh laptop`.\n",
        encoding="utf-8",
    )
    (mem / "n100" / "project_stt.md").write_text(
        "---\nname: speaches\ndescription: local Whisper\n---\nRuns on N100, no laptop.\n",
        encoding="utf-8",
    )
    (mem / "_archive" / "old.md").write_text("laptop laptop laptop", encoding="utf-8")
    today = date.today()
    (mem / "pending.md").write_text(
        "# Pending\n\n## Open\n\n"
        f"- [ ] {today + timedelta(days=5)} — later thing. Action: x\n"
        f"- [ ] {today - timedelta(days=1)} — **overdue** thing [[laptop-ssh]]\n",
        encoding="utf-8",
    )
    (mem / "_archive" / "pending-done.md").write_text("# Closed\n", encoding="utf-8")
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "hub.toml").write_text(
        f'[memory]\ndir = "{mem}"\nmap = "{site}"\n'
        '[[reach]]\nname = "Here"\nlocal = true\n'
        '[[reach]]\nname = "Closed port"\ntcp = "127.0.0.1:9"\n',
        encoding="utf-8",
    )
    hub._conf_cache = (-1.0, {})
    hub._reach.clear()
    yield mem
    hub._conf_cache = (-1.0, {})
    hub._reach.clear()


def test_pending_sorted_close_moves_to_done_log(store):
    items = hub.pending()["items"]
    assert [it["days"] for it in items] == [-1, 5]
    late = items[0]
    left = hub.close_pending(late["id"])["items"]
    assert [it["text"] for it in left] == ["later thing. Action: x"]
    assert "overdue" not in (store / "pending.md").read_text(encoding="utf-8")
    done = (store / "_archive" / "pending-done.md").read_text(encoding="utf-8")
    assert "closed by the user in the web UI" in done and "**overdue** thing" in done
    with pytest.raises(WebUIError) as gone:
        hub.close_pending(late["id"])
    assert gone.value.status == 409


def test_snooze_counts_from_today_when_overdue(store):
    late = hub.pending()["items"][0]
    moved = hub.snooze_pending(late["id"], 3)["items"]
    assert {it["date"] for it in moved} >= {str(date.today() + timedelta(days=3))}
    with pytest.raises(WebUIError):
        hub.snooze_pending(moved[0]["id"], 0)


def test_memory_search_titles_and_archive_excluded(store):
    hits = hub.memory_search("laptop")["results"]
    assert [h["path"] for h in hits] == [
        "n100/reference_laptop.md",
        "n100/project_stt.md",
    ]
    assert hits[0]["title"] == "Laptop SSH"  # from the index pointer
    assert (
        hub.memory_search("laptop whisper")["results"][0]["path"]
        == "n100/project_stt.md"
    )
    assert hub.memory_search("x")["results"] == []


def test_memory_today_without_git_uses_mtime(store):
    over = hub.memory_overview()
    assert over["total"] == 2 and over["domains"] == 1
    assert {d["path"] for d in over["today"]} == {
        "n100/reference_laptop.md",
        "n100/project_stt.md",
    }


def test_memory_and_map_paths_stay_inside(store):
    assert hub.memory_file("n100/reference_laptop.md")["body"].startswith("Use")
    for bad in ("../hub.toml", "/etc/passwd", "_archive/old.md", "pending.md"):
        with pytest.raises(WebUIError):
            hub.memory_file(bad)
    assert hub.map_file("").name == "index.html"
    with pytest.raises(WebUIError):
        hub.map_file("../hub.toml")


async def test_reach_probes_and_remembers_last_ok(store):
    got = await hub.reach(refresh=True)
    rows = {r["name"]: r for r in got["targets"]}
    assert rows["Here"]["ok"] and "mem_avail_mb" in rows["Here"]["detail"]
    assert not rows["Closed port"]["ok"] and rows["Closed port"]["last_ok"] is None
    assert rows["Here"]["last_ok"] == pytest.approx(time.time(), abs=5)


async def test_overview_off_without_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGRAM_DIR", str(tmp_path))
    hub._conf_cache = (-1.0, {})
    assert await hub.overview() == {"enabled": False}


def test_alerts_latest_per_alert_with_the_verdict(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    stamp = lambda m: (now - timedelta(minutes=m)).isoformat().replace("+00:00", "Z")  # noqa: E731
    rows = [
        {
            "ts": (now - timedelta(days=2)).isoformat(),
            "alertname": "Old",
            "disposition": "escalated",
        },
        {
            "ts": stamp(50),
            "alertname": "Disk",
            "instance": "n100",
            "disposition": "flap",
        },
        {
            "ts": stamp(40),
            "alertname": "Down",
            "instance": "nl2",
            "disposition": "serial-flapper-suppressed",
        },
        {  # the triage logs its message just before the escalation line
            "ts": stamp(30),
            "disposition": "tg-delivery-failed",
            "intended_text": "🚨 Down @ nl2\nVERDICT: CRITICAL",
        },
        {
            "ts": stamp(30),
            "alertname": "Down",
            "instance": "nl2",
            "disposition": "escalated",
        },
        "not json",
    ]
    log = tmp_path / "triage.jsonl"
    log.write_text(
        "\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows),
        encoding="utf-8",
    )
    cal = tmp_path / "cal.sh"
    today = date.today().strftime("%d.%m.%Y")
    cal.write_text(
        f"#!/bin/sh\necho '  {today} 10:30 MSK — Митинг'\necho '  {today} (весь день) — Отпуск'\necho '  01.01.2020 09:00 MSK — Давно'\n",
        encoding="utf-8",
    )
    cal.chmod(0o755)
    monkeypatch.setenv("CCGRAM_DIR", str(tmp_path))
    (tmp_path / "hub.toml").write_text(
        f'[alerts]\nlog = "{log}"\n[calendar]\ncommand = "{cal}"\n', encoding="utf-8"
    )
    hub._conf_cache = (-1.0, {})
    hub._calendar_cache = (0.0, None)

    class NowThread:  # run the background refresh inline, after the answer
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(hub.threading, "Thread", NowThread)
    assert hub.happened()["calendar"] is None  # the first call never waits ...
    got = hub.happened()  # ... the refresh it started has filled the cache
    a = got["alerts"]
    assert (a["important"], a["noise"]) == (1, 2)
    assert [(x["name"], x["kind"], x["count"]) for x in a["items"]] == [
        ("Down", "escalated", 2),
        ("Disk", "flap", 1),
    ]
    assert a["items"][0]["verdict"].startswith("🚨 Down")
    assert got["calendar"] == [
        {"day": "today", "time": "10:30", "title": "Митинг"},
        {"day": "today", "time": None, "title": "Отпуск"},
    ]
    hub._conf_cache = (-1.0, {})


def test_drop_pointer_keeps_neighbours(tmp_path):
    index = tmp_path / "n100" / "index.md"
    index.parent.mkdir()
    target = (tmp_path / "n100" / "b.md").resolve()
    text = "\n".join(
        [
            "- [Only B](b.md) — gone line",
            "- [A](a.md) · [B](b.md) · [C](c.md) — trio",
            "- [B first](b.md) / [D](d.md) — pair",
            "- [Other dir](../x/b.md) — not ours",
            "- [A](a.md) — untouched",
        ]
    )
    got = hub._drop_pointer(text, index, target).split("\n")
    assert got == [
        "- [A](a.md) · [C](c.md) — trio",
        "- [D](d.md) — pair",
        "- [Other dir](../x/b.md) — not ours",
        "- [A](a.md) — untouched",
    ]


def test_save_and_forget_are_no_loss(store, tmp_path, monkeypatch):
    backups = tmp_path / "bk"
    (tmp_path / "hub.toml").write_text(
        (tmp_path / "hub.toml")
        .read_text(encoding="utf-8")
        .replace("[memory]\n", f'[memory]\nbackups = "{backups}"\n'),
        encoding="utf-8",
    )
    hub._conf_cache = (-1.0, {})
    rel = "n100/reference_laptop.md"
    opened = hub.memory_file(rel)
    with pytest.raises(WebUIError) as stale:
        hub.memory_save(rel, "new body", None, opened["mtime"] - 10)
    assert stale.value.status == 409
    saved = hub.memory_save(
        rel, "Use `ssh laptop` from N100.", "SSH: «ноут»", opened["mtime"]
    )
    text = (store / rel).read_text(encoding="utf-8")
    assert text.startswith('---\nname: laptop-ssh\ndescription: "SSH: «ноут»"\n---\n')
    assert text.endswith("Use `ssh laptop` from N100.\n")
    assert "Use `ssh laptop`." in Path(saved["backup"]).read_text(encoding="utf-8")

    gone = hub.memory_forget(rel)
    assert not (store / rel).exists()
    assert (store / gone["archived"]).read_text(encoding="utf-8") == text
    assert gone["unlinked"] == ["n100/index.md"]
    assert "reference_laptop.md" not in (store / "n100" / "index.md").read_text(
        encoding="utf-8"
    )
    log = (store / "_archive" / "forgotten.md").read_text(encoding="utf-8")
    assert rel in log and "Laptop SSH" in log
    assert len(list(backups.iterdir())) == 2  # the memory before the edit + the index
    assert hub.memory_overview()["total"] == 1
