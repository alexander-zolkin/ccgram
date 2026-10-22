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


def test_send_all_signs_with_the_stored_key(monkeypatch):
    """Real VAPID signing path (only the HTTP post is faked)."""
    import pywebpush

    posted = []

    class Resp:
        status_code = 201
        text = ""
        headers: dict = {}

    def fake_post(url, data=None, headers=None, timeout=None, **kw):
        posted.append((url, headers))
        return Resp()

    # A real p256dh/auth pair so the payload encryption runs for real.
    import base64
    import os as _os

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    pub = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    push.subscribe(
        {
            "endpoint": "https://push.example/x",
            "keys": {"p256dh": b64(pub), "auth": b64(_os.urandom(16))},
        }
    )
    monkeypatch.setattr(pywebpush.requests, "post", fake_post)
    push._send_all({"title": "Кара", "body": "hi"})
    ((url, headers),) = posted
    assert url == "https://push.example/x"
    assert headers["content-encoding"] == "aes128gcm"
    assert headers["authorization"].startswith("vapid t=")


def test_web_switches_are_independent_and_combine():
    assert push.allowed(5)
    push.set_chat_web(5, False)
    assert not push.allowed(5) and push.allowed(6)
    push.set_web_muted(True)
    assert not push.allowed(6)
    push.set_web_muted(False)
    push.set_chat_web(5, True)
    assert push.allowed(5) and push.web_off() == []
    push.load_web_off([7, 8])
    assert push.web_off() == [7, 8]


async def test_prompt_push_carries_the_fingerprint(monkeypatch):
    calls = []
    monkeypatch.setattr(push, "_send_all", lambda payload: calls.append(payload))
    push.subscribe(SUB)
    push.notify_reply(5, "Чат", "⏳ Approval needed: Bash", prompt="abc123")
    import asyncio

    await asyncio.sleep(0.05)
    assert calls[0]["prompt"] == "abc123" and calls[0]["tid"] == 5
