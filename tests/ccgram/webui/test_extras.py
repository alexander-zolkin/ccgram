from types import SimpleNamespace

import pytest

from ccgram import quiet_threads
from ccgram.webui import extras, push
from ccgram.webui.service import WebUIError


@pytest.fixture(autouse=True)
def _reset():
    extras.reset_for_testing()
    quiet_threads.reset_for_testing()
    yield
    extras.reset_for_testing()
    quiet_threads.reset_for_testing()


class FakeClient:
    def __init__(self):
        self.deleted = []

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


@pytest.fixture
def wired(monkeypatch):
    sent, pushed = [], []
    ids = iter(range(100, 200))

    async def fake_send(client, chat_id, text, **kw):
        sent.append(
            (chat_id, kw["message_thread_id"], text, kw["disable_notification"])
        )
        return SimpleNamespace(message_id=next(ids))

    import ccgram.handlers.messaging_pipeline.message_sender as ms

    monkeypatch.setattr(ms, "rate_limit_send_message", fake_send)
    monkeypatch.setattr(push, "notify_reply", lambda *a: pushed.append(a))
    monkeypatch.setattr(extras, "_targets", lambda wid: [(-100, 7), (None, -3)])
    monkeypatch.setattr(extras, "_title", lambda chat, tid: f"t{tid}")
    return sent, pushed


async def test_each_mode_stop_does_nothing(wired):
    sent, pushed = wired
    await extras.on_stop("@1", FakeClient())
    assert sent == [] and pushed == []
    assert not quiet_threads.is_quiet(-100, 7)


async def test_done_mode_one_ringing_ping_per_turn(wired):
    sent, pushed = wired
    extras.set_notify_mode("done")
    assert quiet_threads.is_quiet(-100, 7)  # turn messages go out silent
    extras.remember_reply(7, "Чат", "первый ответ")
    extras.remember_reply(7, "Чат", "итоговый ответ")
    client = FakeClient()
    await extras.on_stop("@1", client)
    assert sent == [(-100, 7, "✅ Готово", False)]
    assert pushed == [(7, "Чат", "итоговый ответ"), (-3, "t-3", "Готово")]
    await extras.on_stop("@1", client)
    assert client.deleted == [(-100, 100)]  # the previous ping is replaced


async def test_done_mode_respects_muted_topic_and_global_mute(wired):
    sent, pushed = wired
    extras.set_notify_mode("done")
    quiet_threads.set_mode(7, "off")
    quiet_threads.set_all_muted(True)
    await extras.on_stop("@1", FakeClient())
    assert sent == []  # Telegram muted (topic + globally) ...
    assert [p[0] for p in pushed] == [7, -3]  # ... web is its own channel


async def test_attention_rings_in_done_mode_and_pushes(wired):
    sent, pushed = wired
    await extras.on_attention("@1", "Approval needed: Bash", FakeClient())
    assert sent == []  # "each": the interactive prompt itself rings
    assert [p[2] for p in pushed] == ["⏳ Approval needed: Bash"] * 2
    extras.set_notify_mode("done")
    await extras.on_attention("@1", "Approval needed: Bash", FakeClient())
    assert sent == [(-100, 7, "⏳ Approval needed: Bash", False)]


def test_browse_stays_inside_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "proj" / ".git").mkdir(parents=True)
    (home / ".hidden").mkdir()
    monkeypatch.setattr(extras, "_home", lambda: home.resolve())
    got = extras.browse(None)
    assert got["dirs"] == [{"name": "proj", "git": True}]
    assert got["parent"] is None
    assert extras.browse(str(home / "proj"))["git"] is True
    with pytest.raises(WebUIError):
        extras.browse(str(tmp_path))
    with pytest.raises(WebUIError):
        extras.browse(str(home / "missing"))
    assert extras.valid_cwd(None) is None


async def test_pane_status_counts_only_a_running_spinner():
    from unittest.mock import AsyncMock, patch

    sep = "─" * 60
    chrome = f"\n{sep}\n❯ \n{sep}\n  ⏵⏵ auto mode on (shift+tab to cycle)"
    panes = {
        "@1": "● Пишу ответ\n✻ Pondering… (12s · ↓ 1.2k tokens)" + chrome,
        "@2": "● Готово\n✻ Baked for 19s · done 9:42 PM" + chrome,
        "@3": "● Стих\n* Zesting… (52s · ↓ 4.3k tokens)\n  ⎿  Tip: Use /permissions"
        + chrome,
    }
    capture = AsyncMock(side_effect=lambda wid: panes[wid])
    with patch("ccgram.multiplexer.multiplexer", capture_pane=capture):
        assert await extras.pane_status("@1") == "Pondering… (12s · ↓ 1.2k tokens)"
        assert await extras.pane_status("@2") is None
        assert await extras.pane_status("@3") == "Zesting… (52s · ↓ 4.3k tokens)"
