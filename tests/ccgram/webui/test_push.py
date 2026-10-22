import json

import pytest

from ccgram.webui import push

SUB = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGRAM_DIR", str(tmp_path))
    push.reset_for_testing()
    yield
    push.reset_for_testing()


def test_keys_are_generated_once_and_persisted(tmp_path):
    key = push.public_key()
    assert len(key) > 80 and "=" not in key
    push.reset_for_testing()
    assert push.public_key() == key
    stored = json.loads((tmp_path / "webpush.json").read_text())
    assert stored["private_pem"].startswith("-----BEGIN")
    assert oct((tmp_path / "webpush.json").stat().st_mode & 0o777) == "0o600"


def test_subscribe_validates_and_dedupes():
    for bad in (
        None,
        {},
        {"endpoint": "http://x", "keys": SUB["keys"]},
        {"endpoint": SUB["endpoint"]},
    ):
        with pytest.raises(ValueError):
            push.subscribe(bad)
    assert push.subscribe(SUB) == 1
    assert push.subscribe(dict(SUB)) == 1
    assert push.has_subscriptions()
    push.unsubscribe(SUB["endpoint"])
    assert not push.has_subscriptions()


def test_visible_tab_holds_pushes_back():
    assert not push.someone_watching()
    push.mark_visible("c1", True)
    assert push.someone_watching()
    push.mark_visible("c1", False)
    assert not push.someone_watching()


def test_send_all_drops_gone_endpoints(monkeypatch):
    import pywebpush

    push.subscribe(SUB)
    push.subscribe({**SUB, "endpoint": "https://push.example/live"})
    sent = []

    class Resp:
        status_code = 410

    def fake_webpush(subscription_info, **kw):
        sent.append((subscription_info["endpoint"], json.loads(kw["data"])["title"]))
        if subscription_info["endpoint"].endswith("/abc"):
            raise pywebpush.WebPushException("gone", response=Resp())

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    push._send_all({"title": "Кара", "body": "hi"})
    assert {e for e, _ in sent} == {SUB["endpoint"], "https://push.example/live"}
    assert [s["endpoint"] for s in push._load()["subscriptions"]] == [
        "https://push.example/live"
    ]


async def test_notify_reply_skips_when_watched_or_unsubscribed(monkeypatch):
    calls = []
    monkeypatch.setattr(push, "_send_all", lambda payload: calls.append(payload))
    push.notify_reply(1, "t", "text")  # no subscriptions
    push.subscribe(SUB)
    push.mark_visible("c", True)
    push.notify_reply(1, "t", "text")  # a tab is on screen
    push.mark_visible("c", False)
    push.notify_reply(5, "Чат", "  long\n reply  ")
    import asyncio

    await asyncio.sleep(0.05)
    assert calls == [{"title": "Чат", "body": "long reply", "tag": "t5", "url": "/#5"}]
