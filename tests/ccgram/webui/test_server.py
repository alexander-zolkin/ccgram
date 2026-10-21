import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from ccgram.webui import service
from ccgram.webui.auth import (
    MAX_FAILURES,
    LoginThrottle,
    issue_cookie,
    verify_cookie,
)
from ccgram.webui.bus import EventBus, bus, publish_new_message
from ccgram.webui.server import build_app

BOT = "1234:abcdef"
PASSWORD = "s3cret"
POST_HEADERS = {"X-CCGram": "1"}

SESSIONS = [
    {
        "thread_id": 7,
        "chat_id": -100,
        "window_id": "@3",
        "name": "kara 10-21 12-00",
        "state": "idle",
        "provider": "claude",
        "cwd": "/tmp",
        "last_activity": 1.0,
    }
]


@pytest.fixture
def calls(monkeypatch):
    log: list[tuple] = []

    async def fake_send(bot, tid, text):
        log.append(("send", tid, text))
        return {"ok": True, "woke": False, "window_id": "@3"}

    async def fake_history(tid, limit):
        if tid != 7:
            raise service.WebUIError("unknown session", 404)
        return {"messages": [{"role": "user", "text": "hi"}], "total": 1}

    async def fake_create(bot, text, name, model, provider="claude"):
        log.append(("create", text, name))
        return {"thread_id": 8, "window_id": "@4", "name": name or "kara"}

    async def fake_list():
        return SESSIONS

    monkeypatch.setattr(service, "list_sessions", fake_list)
    monkeypatch.setattr(service, "send_text", fake_send)
    monkeypatch.setattr(service, "history", fake_history)
    monkeypatch.setattr(service, "create_session", fake_create)
    return log


@pytest.fixture
async def client(calls):
    app = build_app(bot=object(), bot_token=BOT, password=PASSWORD)
    async with TestClient(TestServer(app)) as c:
        yield c


async def _login(client: TestClient) -> None:
    resp = await client.post(
        "/api/login", json={"password": PASSWORD}, headers=POST_HEADERS
    )
    assert resp.status == 200


# ── auth primitives ──────────────────────────────────────────────────────────


def test_cookie_roundtrip_and_tamper():
    c = issue_cookie(BOT, PASSWORD, now=1000)
    assert verify_cookie(c, BOT, PASSWORD, now=1001)
    assert not verify_cookie(c, BOT, "other-password", now=1001)
    assert not verify_cookie(c + "0", BOT, PASSWORD, now=1001)
    assert not verify_cookie("9999999999.deadbeef", BOT, PASSWORD, now=1001)
    assert not verify_cookie(None, BOT, PASSWORD)


def test_cookie_expires():
    c = issue_cookie(BOT, PASSWORD, now=1000)
    assert not verify_cookie(c, BOT, PASSWORD, now=1000 + 31 * 24 * 3600)


def test_throttle_blocks_after_max_failures():
    t = LoginThrottle()
    for _ in range(MAX_FAILURES):
        assert not t.blocked("1.2.3.4", now=100)
        t.record_failure("1.2.3.4", now=100)
    assert t.blocked("1.2.3.4", now=100)
    assert not t.blocked("5.6.7.8", now=100)
    assert not t.blocked("1.2.3.4", now=100 + 16 * 60)


# ── HTTP surface ─────────────────────────────────────────────────────────────


async def test_index_is_public_and_has_security_headers(client):
    resp = await client.get("/")
    assert resp.status == 200
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]


async def test_api_requires_login(client):
    for path in ("/api/me", "/api/sessions", "/api/sessions/7/messages"):
        resp = await client.get(path)
        assert resp.status == 401, path


async def test_post_requires_csrf_header(client):
    resp = await client.post("/api/login", json={"password": PASSWORD})
    assert resp.status == 403


async def test_wrong_password_then_lockout(client):
    for _ in range(MAX_FAILURES):
        resp = await client.post(
            "/api/login", json={"password": "nope"}, headers=POST_HEADERS
        )
        assert resp.status == 401
    resp = await client.post(
        "/api/login", json={"password": PASSWORD}, headers=POST_HEADERS
    )
    assert resp.status == 429


async def test_login_sets_httponly_strict_cookie(client):
    resp = await client.post(
        "/api/login", json={"password": PASSWORD}, headers=POST_HEADERS
    )
    cookie = resp.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie


async def test_sessions_and_history_after_login(client):
    await _login(client)
    resp = await client.get("/api/sessions")
    assert (await resp.json())["sessions"] == SESSIONS
    resp = await client.get("/api/sessions/7/messages")
    assert (await resp.json())["total"] == 1
    resp = await client.get("/api/sessions/99/messages")
    assert resp.status == 404
    resp = await client.get("/api/sessions/abc/messages")
    assert resp.status == 400


async def test_send_and_create(client, calls):
    await _login(client)
    resp = await client.post(
        "/api/sessions/7/send", json={"text": "hello"}, headers=POST_HEADERS
    )
    assert resp.status == 200
    resp = await client.post(
        "/api/sessions", json={"text": "go", "name": "demo"}, headers=POST_HEADERS
    )
    assert (await resp.json())["thread_id"] == 8
    assert calls == [("send", 7, "hello"), ("create", "go", "demo")]


async def test_invalid_json_is_400(client):
    await _login(client)
    resp = await client.post(
        "/api/sessions/7/send",
        data="not json",
        headers={**POST_HEADERS, "Content-Type": "application/json"},
    )
    assert resp.status == 400


async def test_events_stream_sessions_then_messages(client):
    await _login(client)
    resp = await client.get("/api/events")
    assert resp.status == 200
    first = await asyncio.wait_for(resp.content.readline(), 2)
    assert json.loads(first.decode()[len("data: ") :])["type"] == "sessions"
    await resp.content.readline()  # blank separator
    msg = SimpleNamespace(
        role="assistant",
        text="done",
        content_type="text",
        tool_name=None,
        tool_use_id=None,
        is_complete=True,
    )
    publish_new_message(msg, [(1, "@3", 7, -100)])
    line = await asyncio.wait_for(resp.content.readline(), 2)
    event = json.loads(line.decode()[len("data: ") :])
    assert event["type"] == "message"
    assert event["thread_id"] == 7
    assert event["text"] == "done"
    resp.close()


# ── bus ──────────────────────────────────────────────────────────────────────


def test_bus_overflow_turns_into_resync():
    b = EventBus()
    q = b.subscribe()
    for i in range(600):
        b.publish({"type": "message", "i": i})
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert {"type": "resync"} in items
    assert len(items) <= 500


def test_publish_dedupes_topics_and_skips_without_subscribers():
    msg = SimpleNamespace(
        role="user",
        text="x",
        content_type="text",
        tool_name=None,
        tool_use_id=None,
        is_complete=True,
    )
    # no subscribers: no-op, no error
    publish_new_message(msg, [(1, "@3", 7, -100)])
    q = bus.subscribe()
    try:
        publish_new_message(msg, [(1, "@3", 7, -100), (2, "@3", 7, -100)])
        assert q.qsize() == 1
    finally:
        bus.unsubscribe(q)


def test_key_whitelist():
    assert "Enter" in service.ALLOWED_KEYS
    assert "1" in service.ALLOWED_KEYS
    assert "rm -rf" not in service.ALLOWED_KEYS


async def test_entries_rename_upload_routes(client, monkeypatch):
    seen = {}

    async def fake_entries(tid, *, limit, before, after):
        seen["entries"] = (tid, limit, before, after)
        return {"items": [], "start": 0, "end": 10, "structured": True}

    async def fake_rename(bot, tid, name):
        seen["rename"] = (tid, name)
        return {"ok": True, "name": name}

    def fake_save(tid, filename, data):
        seen["upload"] = (tid, filename, data)
        return {"path": ".ccgram-uploads/x", "name": filename, "image": False}

    monkeypatch.setattr(service, "entries", fake_entries)
    monkeypatch.setattr(service, "rename", fake_rename)
    monkeypatch.setattr(service, "save_upload", fake_save)
    await _login(client)
    resp = await client.get("/api/sessions/7/entries?limit=20&after=5")
    assert (await resp.json())["end"] == 10
    assert seen["entries"] == (7, 20, None, 5)
    resp = await client.get("/api/sessions/7/entries?before=abc")
    assert resp.status == 400
    resp = await client.post(
        "/api/sessions/7/rename", json={"name": "Новое"}, headers=POST_HEADERS
    )
    assert seen["rename"] == (7, "Новое")
    import aiohttp

    form = aiohttp.FormData()
    form.add_field("file", b"hello", filename="a.txt")
    resp = await client.post("/api/sessions/7/upload", data=form, headers=POST_HEADERS)
    assert resp.status == 200
    assert seen["upload"] == (7, "a.txt", b"hello")


def test_save_upload_sanitizes_and_stays_inside(tmp_path, monkeypatch):
    b = service.Binding(1, -100, 7, "@3")
    monkeypatch.setattr(service, "find_binding", lambda tid: b)
    monkeypatch.setattr(service, "_cwd_for", lambda binding: str(tmp_path))
    res = service.save_upload(7, "../../etc/pass wd?.png", b"data")
    saved = tmp_path / res["path"]
    assert saved.read_bytes() == b"data"
    assert saved.parent == tmp_path / ".ccgram-uploads"
    assert res["image"] is True
    import pytest as _pytest

    with _pytest.raises(service.WebUIError):
        service.save_upload(7, "big.bin", b"x" * (service.MAX_UPLOAD + 1))


async def test_session_menu_routes(client, monkeypatch):
    seen = []

    async def rec(name, *args):
        seen.append((name, *args))
        return {"ok": True}

    monkeypatch.setattr(service, "set_pin", lambda tid, on: rec("pin", tid, on))
    monkeypatch.setattr(
        service, "remove_from_telegram", lambda bot, tid: rec("detach", tid)
    )
    monkeypatch.setattr(service, "delete_session", lambda bot, tid: rec("delete", tid))

    async def models():
        return {"models": [{"id": "m1", "name": "M1"}], "default": "m1"}

    monkeypatch.setattr(service, "list_models", models)
    monkeypatch.setattr(
        service, "save_upload", lambda tid, fn, data: {"path": f"x/{fn}", "tid": tid}
    )
    await _login(client)
    for path, body in (("pin", {"pinned": True}), ("detach", {}), ("delete", {})):
        resp = await client.post(
            f"/api/sessions/7/{path}", json=body, headers=POST_HEADERS
        )
        assert resp.status == 200, path
    assert seen == [("pin", 7, True), ("detach", 7), ("delete", 7)]
    assert (await (await client.get("/api/models")).json())["default"] == "m1"
    import aiohttp

    form = aiohttp.FormData()
    form.add_field("file", b"x", filename="n.txt")
    resp = await client.post("/api/uploads", data=form, headers=POST_HEADERS)
    assert (await resp.json())["tid"] is None


def test_valid_model():
    assert service.valid_model("claude-opus-5-5") == "claude-opus-5-5"
    assert service.valid_model("claude-opus-5-5[1m]") == "claude-opus-5-5[1m]"
    assert service.valid_model("x; rm -rf /") is None
    assert service.valid_model(None) is None


async def test_keep_route(client, monkeypatch):
    async def keep(tid):
        return {"ok": True, "tid": tid}

    monkeypatch.setattr(service, "keep_web_only", keep)
    await _login(client)
    resp = await client.post("/api/sessions/42/keep", json={}, headers=POST_HEADERS)
    assert (await resp.json())["tid"] == 42
