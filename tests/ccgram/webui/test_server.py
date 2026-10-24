"""The hub API (CCGRAM-HOTFIX:hub-api): Kara Hub's token, its routes only."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from ccgram import quiet_threads
from ccgram.webui import extras, prefs, registry, server, service
from ccgram.webui.server import build_app

POST = {"X-CCGram": "1"}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGRAM_DIR", str(tmp_path))
    prefs.reset_for_testing()
    registry.reset_for_testing()
    quiet_threads.reset_for_testing()
    extras.reset_for_testing()
    server._creates.clear()  # create keys outlive a test otherwise
    yield
    prefs.reset_for_testing()
    registry.reset_for_testing()
    quiet_threads.reset_for_testing()
    extras.reset_for_testing()


@pytest.fixture
def calls(monkeypatch):
    log: list[tuple] = []

    async def fake_send(bot, tid, text):
        log.append(("send", tid, text))
        return {"ok": True, "woke": False, "window_id": "@3"}

    async def fake_create(
        bot, text, name, model, provider="claude", effort=None, cwd=None, resume_id=None
    ):
        log.append(("create", text, name) + ((effort,) if effort else ()))
        return {"thread_id": 8, "window_id": "@4", "name": name or "kara"}

    async def fake_rename(bot, tid, name):
        log.append(("rename", tid, name))
        return {"ok": True, "name": name}

    async def fake_detach(bot, tid):
        log.append(("detach", tid))
        return {"ok": True}

    async def fake_delete(bot, tid):
        log.append(("delete", tid))
        return {"ok": True}

    monkeypatch.setattr(service, "send_text", fake_send)
    monkeypatch.setattr(service, "create_session", fake_create)
    monkeypatch.setattr(service, "rename", fake_rename)
    monkeypatch.setattr(service, "remove_from_telegram", fake_detach)
    monkeypatch.setattr(service, "delete_session", fake_delete)
    return log


@pytest.fixture
async def client(calls):
    app = build_app(bot=object())
    async with TestClient(TestServer(app)) as c:
        yield c


@pytest.fixture
def hub():
    """Headers the hub's adapter sends (kara_hub/adapter.py: old_web)."""
    return {**POST, server.HUB_TOKEN_HEADER: server._hub_token()}


def test_hub_token_is_made_once_0600(tmp_path):
    token = server._hub_token()
    assert token and token == server._hub_token()
    path = tmp_path / "hub_token"
    assert path.read_text().strip() == token
    assert path.stat().st_mode & 0o777 == 0o600


async def test_token_required_and_scoped(client, hub, calls):
    resp = await client.post("/api/sessions/7/send", json={"text": "hi"}, headers=hub)
    assert resp.status == 200 and ("send", 7, "hi") in calls
    wrong = {**hub, server.HUB_TOKEN_HEADER: hub[server.HUB_TOKEN_HEADER] + "x"}
    assert (
        await client.post("/api/sessions/7/send", json={}, headers=wrong)
    ).status == 401
    assert (
        await client.post("/api/sessions/7/send", json={}, headers=POST)
    ).status == 401
    # No page and nothing else: the old web's routes are gone, even with the token.
    for path in (
        "/",
        "/api/me",
        "/api/sessions",
        "/api/sessions/7/entries",
        "/api/events",
    ):
        assert (await client.get(path, headers=hub)).status == 401, path
    for path in ("/api/login", "/api/sessions/7/key", "/api/push/subscribe"):
        assert (await client.post(path, json={}, headers=hub)).status == 401, path


async def test_post_requires_csrf_header(client, hub):
    no_csrf = {server.HUB_TOKEN_HEADER: hub[server.HUB_TOKEN_HEADER]}
    resp = await client.post(
        "/api/sessions/7/send", json={"text": "x"}, headers=no_csrf
    )
    assert resp.status == 403


async def test_invalid_json_and_bad_id(client, hub):
    resp = await client.post(
        "/api/sessions/7/send",
        data="not json",
        headers={**hub, "Content-Type": "application/json"},
    )
    assert resp.status == 400
    resp = await client.post("/api/sessions/abc/send", json={"text": "x"}, headers=hub)
    assert resp.status == 401  # not a hub route at all


async def test_every_route_the_hub_adapter_calls(client, hub, calls, monkeypatch):
    """The contract with kara_hub/adapter.py: each of its calls answers 200."""

    async def fake_probe(bot, tid):
        return {"exists": True}

    async def fake_keep(tid):
        return {"ok": True, "tid": tid}

    async def fake_sound(tid, mode):
        return {"ok": True, "sound": mode}

    monkeypatch.setattr(extras, "probe_topic", fake_probe)
    monkeypatch.setattr(service, "keep_web_only", fake_keep)
    monkeypatch.setattr(service, "set_sound", fake_sound)
    for path, body in (
        ("rename", {"name": "Новое"}),
        ("detach", {}),
        ("keep", {}),
        ("delete", {}),
        ("sound", {"mode": "off"}),
        ("send", {"text": "wake up"}),
        ("probe", {}),
    ):
        resp = await client.post(f"/api/sessions/7/{path}", json=body, headers=hub)
        assert resp.status == 200, path
    resp = await client.post(
        "/api/sessions",
        json={"text": "go", "cwd": None, "web_only": False, "key": "k-0123456789"},
        headers=hub,
    )
    assert (await resp.json())["window_id"] == "@4"
    assert (await client.get("/api/settings", headers=hub)).status == 200
    resp = await client.post("/api/settings", json={"tg_relay": "final"}, headers=hub)
    assert (await resp.json())["tg_relay"] == "final"
    assert ("rename", 7, "Новое") in calls and ("detach", 7) in calls
    assert ("delete", 7) in calls and ("send", 7, "wake up") in calls


async def test_create_with_effort_and_no_web_only(client, hub, calls):
    for effort, want in (
        ("low", ("create", "x", None, "low")),
        ("bogus", ("create", "x", None)),
    ):
        await client.post(
            "/api/sessions", json={"text": "x", "effort": effort}, headers=hub
        )
        assert calls[-1] == want
    # Chats without a topic and other machines are made by the hub itself.
    resp = await client.post(
        "/api/sessions", json={"text": "x", "web_only": True}, headers=hub
    )
    assert resp.status == 400
    resp = await client.post(
        "/api/sessions", json={"text": "x", "device": "pc"}, headers=hub
    )
    assert resp.status == 400


async def test_create_key_gives_one_session(client, hub, monkeypatch):
    made: list[int] = []
    gate = asyncio.Event()

    async def slow_create(bot, text, name, model, *args, **kwargs):
        made.append(8 + len(made))
        tid = made[-1]
        await gate.wait()
        if text == "fail":
            raise service.WebUIError("launch failed", 502)
        return {"thread_id": tid, "window_id": "@4", "name": "kara"}

    monkeypatch.setattr(service, "create_session", slow_create)
    body = {"text": "go", "key": "k-0123456789"}
    # The hub's request dropped; it asks again while the first one runs.
    first = asyncio.ensure_future(client.post("/api/sessions", json=body, headers=hub))
    await asyncio.sleep(0.05)
    second = asyncio.ensure_future(client.post("/api/sessions", json=body, headers=hub))
    await asyncio.sleep(0.05)
    gate.set()
    for resp in (await first, await second):
        assert (await resp.json())["thread_id"] == 8
    resp = await client.post("/api/sessions", json=body, headers=hub)
    assert (await resp.json())["thread_id"] == 8
    assert made == [8]
    resp = await client.post(
        "/api/sessions", json={**body, "key": "k-another-one"}, headers=hub
    )
    assert (await resp.json())["thread_id"] == 9
    # A failed create may be retried under the same key.
    fail = {"text": "fail", "key": "k-failing-key"}
    resp = await client.post("/api/sessions", json=fail, headers=hub)
    assert resp.status == 502
    resp = await client.post("/api/sessions", json=fail, headers=hub)
    assert resp.status == 502
    assert made == [8, 9, 10, 11]


async def test_settings_roundtrip(client, hub):
    got = await (await client.get("/api/settings", headers=hub)).json()
    assert got == {
        "tg_muted": False,
        "tg_relay": "all",
        "new_chat_telegram": True,
        "notify_mode": "done",  # only a turn's last reply rings
    }
    resp = await client.post(
        "/api/settings",
        json={"new_chat_telegram": False, "tg_muted": True, "notify_mode": "each"},
        headers=hub,
    )
    got = await resp.json()
    assert got["tg_muted"] is True and got["new_chat_telegram"] is False
    assert got["notify_mode"] == "each" and quiet_threads.is_quiet(-1, 5)
    prefs.reset_for_testing()
    assert prefs.new_chat_telegram() is False and prefs.tg_muted() is True
    assert prefs.notify_mode() == "each"


@pytest.fixture
async def real_client():
    """The app over the real service functions (no ``calls`` fakes)."""
    async with TestClient(TestServer(build_app(bot=object()))) as c:
        yield c


async def test_web_only_session_through_real_service(
    real_client, hub, tmp_path, monkeypatch
):
    """Rename / sound / probe / keep / delete of a web-only session, unfaked."""
    client = real_client

    async def asleep(_wid):
        return False

    monkeypatch.setattr(service, "_window_alive", asleep)  # never touch tmux
    monkeypatch.setattr(service, "_bindings", lambda: [])
    registry.detach(
        42,
        "@9",
        name="kara 10-24 12-00",
        cwd=str(tmp_path),
        provider="claude",
        approval_mode="normal",
        session_id="s-42",
        transcript_path="",
    )
    resp = await client.post(
        "/api/sessions/42/rename", json={"name": "Итог"}, headers=hub
    )
    assert (await resp.json())["name"] == "Итог"
    resp = await client.post(
        "/api/sessions/42/sound", json={"mode": "off"}, headers=hub
    )
    assert (await resp.json())["sound"] == "off"
    # The hub reads the sound map straight from the file.
    saved = json.loads((tmp_path / "webui_prefs.json").read_text())
    assert saved["sound"] == {"42": "off"}
    resp = await client.post("/api/sessions/42/probe", json={}, headers=hub)
    assert (await resp.json()) == {"exists": None}
    resp = await client.post("/api/sessions/42/keep", json={}, headers=hub)
    assert resp.status == 200
    entry = json.loads((tmp_path / "webui_sessions.json").read_text())["42"]
    assert entry["origin"] == "kept" and entry["name"] == "Итог"
    resp = await client.post("/api/sessions/42/delete", json={}, headers=hub)
    assert resp.status == 200
    assert json.loads((tmp_path / "webui_sessions.json").read_text()) == {}
    resp = await client.post("/api/sessions/42/keep", json={}, headers=hub)
    assert resp.status == 404


async def test_settings_keep_unknown_pref_keys(client, hub, tmp_path):
    """Keys only the old page used stay in webui_prefs.json untouched."""
    (tmp_path / "webui_prefs.json").write_text(
        json.dumps({"pinned": [7], "web_off": [3], "sound": {"7": "on"}})
    )
    prefs.reset_for_testing()
    await client.post("/api/settings", json={"tg_muted": True}, headers=hub)
    saved = json.loads((tmp_path / "webui_prefs.json").read_text())
    assert saved == {
        "pinned": [7],
        "web_off": [3],
        "sound": {"7": "on"},
        "tg_muted": True,
    }


async def test_start_restores_telegram_prefs(tmp_path, monkeypatch):
    from ccgram.config import config

    (tmp_path / "webui_prefs.json").write_text(
        json.dumps(
            {
                "sound": {"7": "off"},
                "tg_muted": True,
                "tg_relay": "final",
                "notify_mode": "each",
            }
        )
    )
    prefs.reset_for_testing()
    monkeypatch.setattr(config, "webui_port", 0)
    await server.start_webui(object(), {})  # off: nothing restored, nothing bound
    assert quiet_threads.mode(7) == "auto" and not quiet_threads.all_muted()
    server.restore_telegram_prefs()
    assert quiet_threads.mode(7) == "off" and quiet_threads.all_muted()
    assert quiet_threads.relay() == "final" and extras.notify_mode() == "each"
