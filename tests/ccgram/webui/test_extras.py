from types import SimpleNamespace

import pytest

from ccgram import quiet_threads
from ccgram.webui import extras
from ccgram.webui.service import WebUIError


@pytest.fixture(autouse=True)
def _reset():
    quiet_threads.reset_for_testing()
    extras.reset_for_testing()  # re-applies the default mode's turn-quiet
    yield
    quiet_threads.reset_for_testing()
    extras.reset_for_testing()


class FakeClient:
    def __init__(self):
        self.deleted = []

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


@pytest.fixture
def sent(monkeypatch):
    out = []
    ids = iter(range(100, 200))

    async def fake_send(client, chat_id, text, **kw):
        out.append((chat_id, kw["message_thread_id"], text, kw["disable_notification"]))
        return SimpleNamespace(message_id=next(ids))

    import ccgram.handlers.messaging_pipeline.message_sender as ms

    monkeypatch.setattr(ms, "rate_limit_send_message", fake_send)
    monkeypatch.setattr(extras, "_targets", lambda wid: [(-100, 7)])
    return out


async def test_each_mode_stop_does_nothing(sent):
    extras.set_notify_mode("each")
    await extras.on_stop("@1", FakeClient())
    assert sent == []
    assert not quiet_threads.is_quiet(-100, 7)


async def test_done_is_the_default_and_only_the_final_reply_rings(sent):
    assert extras.notify_mode() == "done"
    assert quiet_threads.is_quiet(-100, 7)  # turn messages go out silent ...
    with quiet_threads.final_reply():
        assert not quiet_threads.is_quiet(-100, 7)  # ... except the last reply
    client = FakeClient()
    await extras.on_attention("@1", "Approval needed: Bash", client)
    assert sent == [(-100, 7, "⏳ Approval needed: Bash", False)]
    await extras.on_attention("@1", "Approval needed: Edit", client)
    assert client.deleted == [(-100, 100)]  # one ping per topic
    await extras.on_stop("@1", client)
    assert len(sent) == 2  # no extra "done" message: the reply itself rang
    assert client.deleted == [(-100, 100), (-100, 101)]  # the answered ping goes


async def test_done_mode_respects_muted_topic_and_global_mute(sent):
    extras.set_notify_mode("done")
    quiet_threads.set_mode(7, "off")
    await extras.on_attention("@1", "Approval needed: Bash", FakeClient())
    assert sent == []  # the topic is muted
    quiet_threads.set_mode(7, "auto")
    quiet_threads.set_all_muted(True)
    await extras.on_attention("@1", "Approval needed: Bash", FakeClient())
    assert sent == []  # Telegram muted globally


async def test_attention_rings_only_in_done_mode(sent):
    extras.set_notify_mode("each")
    await extras.on_attention("@1", "Approval needed: Bash", FakeClient())
    assert sent == []  # "each": the interactive prompt itself rings
    extras.set_notify_mode("done")
    await extras.on_attention("@1", "Approval needed: Bash", FakeClient())
    assert sent == [(-100, 7, "⏳ Approval needed: Bash", False)]


def test_targets_are_the_topics_of_the_window(monkeypatch):
    from ccgram.thread_router import thread_router

    monkeypatch.setattr(
        type(thread_router),
        "iter_thread_bindings",
        lambda self: iter([(1, 7, "@1"), (1, 8, "@2"), (1, 9, "@1")]),
        raising=False,
    )
    monkeypatch.setattr(
        type(thread_router),
        "resolve_chat_id",
        lambda self, user, thread: None if thread == 9 else -100,
        raising=False,
    )
    assert extras._targets("@1") == [(-100, 7)]


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
    assert extras.valid_cwd(str(home / "proj")) == str((home / "proj").resolve())
    with pytest.raises(WebUIError):
        extras.valid_cwd(str(tmp_path))
