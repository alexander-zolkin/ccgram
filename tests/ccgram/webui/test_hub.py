import time
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
