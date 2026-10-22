"""Passkey + Telegram login, end to end through the HTTP API.

A tiny software authenticator (P-256, "none" attestation) answers the
WebAuthn ceremonies, so registration and login are verified for real.
"""

import base64
import hashlib
import json
import os
from types import SimpleNamespace

import cbor2
import pytest
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from ccgram.webui import passkeys, prefs, tg_login
from ccgram.webui.server import build_app

BOT = "1234:abcdef"
PASSWORD = "s3cret"
H = {"X-CCGram": "1"}


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SoftKey:
    """Face ID in a test: one discoverable P-256 credential."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(16)
        self.count = 0

    def _auth_data(self, rp_id: str, attested: bool) -> bytes:
        flags = 0x01 | 0x04 | (0x40 if attested else 0)  # UP, UV, AT
        data = hashlib.sha256(rp_id.encode()).digest() + bytes([flags])
        data += self.count.to_bytes(4, "big")
        if attested:
            nums = self.key.public_key().public_numbers()
            cose = {
                1: 2,
                3: -7,
                -1: 1,
                -2: nums.x.to_bytes(32, "big"),
                -3: nums.y.to_bytes(32, "big"),
            }
            data += bytes(16) + len(self.cred_id).to_bytes(2, "big") + self.cred_id
            data += cbor2.dumps(cose)
        return data

    @staticmethod
    def _client_data(kind: str, options: dict, origin: str) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": options["challenge"], "origin": origin}
        ).encode()

    def create(self, options: dict, origin: str) -> dict:
        cdj = self._client_data("webauthn.create", options, origin)
        att = cbor2.dumps(
            {
                "fmt": "none",
                "attStmt": {},
                "authData": self._auth_data(options["rp"]["id"], True),
            }
        )
        return {
            "id": b64u(self.cred_id),
            "rawId": b64u(self.cred_id),
            "type": "public-key",
            "response": {"clientDataJSON": b64u(cdj), "attestationObject": b64u(att)},
            "clientExtensionResults": {},
        }

    def get(self, options: dict, origin: str) -> dict:
        self.count += 1
        cdj = self._client_data("webauthn.get", options, origin)
        auth = self._auth_data(options["rpId"], False)
        sig = self.key.sign(
            auth + hashlib.sha256(cdj).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": b64u(self.cred_id),
            "rawId": b64u(self.cred_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64u(cdj),
                "authenticatorData": b64u(auth),
                "signature": b64u(sig),
            },
            "clientExtensionResults": {},
        }


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, **kw):
        self.sent.append(kw)


@pytest.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGRAM_DIR", str(tmp_path))
    monkeypatch.setattr(prefs, "_prefs", None)
    monkeypatch.setattr(tg_login.config, "group_id", -100)
    passkeys.reset_for_testing()
    tg_login.reset_for_testing()
    bot = FakeBot()
    app = build_app(bot=bot, bot_token=BOT, password=PASSWORD)
    async with TestClient(TestServer(app)) as client:
        origin = f"http://{client.host}:{client.port}"
        yield SimpleNamespace(client=client, bot=bot, origin=origin, tmp=tmp_path)
    tg_login.reset_for_testing()


async def _password_login(c):
    resp = await c.post("/api/login", json={"password": PASSWORD}, headers=H)
    assert resp.status == 200


async def _add_key(e, key: SoftKey):
    resp = await e.client.post("/api/auth/passkeys/options", json={}, headers=H)
    begin = await resp.json()
    cred = key.create(begin["options"], e.origin)
    resp = await e.client.post(
        "/api/auth/passkeys",
        json={"token": begin["token"], "credential": cred, "name": "iPhone"},
        headers=H,
    )
    assert resp.status == 200, await resp.text()
    return await resp.json()


async def _passkey_login(e, key: SoftKey):
    resp = await e.client.post("/api/auth/passkey/login/options", json={}, headers=H)
    begin = await resp.json()
    cred = key.get(begin["options"], e.origin)
    return await e.client.post(
        "/api/auth/passkey/login",
        json={"token": begin["token"], "credential": cred},
        headers=H,
    )


async def test_register_then_log_in_with_the_key(env):
    key = SoftKey()
    methods = await (await env.client.get("/api/auth/methods")).json()
    assert methods == {"password": True, "passkey": False, "telegram": True}
    resp = await env.client.post("/api/auth/passkeys/options", json={}, headers=H)
    assert resp.status == 401  # adding a key needs a session
    await _password_login(env.client)
    added = await _add_key(env, key)
    assert [k["name"] for k in added["keys"]] == ["iPhone"]
    stored = json.loads((env.tmp / "webui_passkeys.json").read_text())
    assert "public_key" in stored["credentials"][0]
    assert oct((env.tmp / "webui_passkeys.json").stat().st_mode & 0o777) == "0o600"

    env.client.session.cookie_jar.clear()
    assert (await env.client.get("/api/me")).status == 401
    resp = await _passkey_login(env, key)
    assert resp.status == 200, await resp.text()
    assert (await env.client.get("/api/me")).status == 200


async def test_a_foreign_key_or_replayed_challenge_fails(env):
    await _password_login(env.client)
    await _add_key(env, SoftKey())
    env.client.session.cookie_jar.clear()
    assert (await _passkey_login(env, SoftKey())).status == 401
    # a challenge token is single-use
    resp = await env.client.post("/api/auth/passkey/login/options", json={}, headers=H)
    begin = await resp.json()
    body = {
        "token": begin["token"],
        "credential": SoftKey().get(begin["options"], env.origin),
    }
    await env.client.post("/api/auth/passkey/login", json=body, headers=H)
    resp = await env.client.post("/api/auth/passkey/login", json=body, headers=H)
    assert resp.status == 401
    assert (await env.client.get("/api/me")).status == 401


async def test_change_password_logs_others_out_and_keeps_this_browser(env):
    await _password_login(env.client)
    other = TestClient(TestServer(env.client.server.app))  # a second browser
    await other.start_server()
    try:
        await _password_login(other)
        bad = {"current": "nope", "new": "my-own-pass-1"}
        resp = await env.client.post("/api/security/password", json=bad, headers=H)
        assert resp.status == 401
        short = {"current": PASSWORD, "new": "short"}
        resp = await env.client.post("/api/security/password", json=short, headers=H)
        assert resp.status == 400
        good = {"current": PASSWORD, "new": "my-own-pass-1"}
        resp = await env.client.post("/api/security/password", json=good, headers=H)
        assert resp.status == 200 and (await resp.json())["password_set_here"]
        assert (await env.client.get("/api/me")).status == 200  # fresh cookie
        assert (await other.get("/api/me")).status == 401  # rotated out
        stored = (env.tmp / "webui_auth.json").read_text()
        assert "my-own-pass-1" not in stored
        resp = await other.post("/api/login", json={"password": PASSWORD}, headers=H)
        assert resp.status == 401
        resp = await other.post(
            "/api/login", json={"password": "my-own-pass-1"}, headers=H
        )
        assert resp.status == 200
        await env.client.post("/api/security/sign-out-others", json={}, headers=H)
        assert (await other.get("/api/me")).status == 401
        assert (await env.client.get("/api/me")).status == 200
    finally:
        await other.close()


async def test_password_change_proven_by_a_passkey(env):
    await _password_login(env.client)
    key = SoftKey()
    await _add_key(env, key)
    resp = await env.client.post("/api/auth/passkey/login/options", json={}, headers=H)
    begin = await resp.json()
    proof = {
        "token": begin["token"],
        "credential": key.get(begin["options"], env.origin),
    }
    body = {"passkey": proof, "new": "via-face-id-9"}
    resp = await env.client.post("/api/security/password", json=body, headers=H)
    assert resp.status == 200
    sec = await (await env.client.get("/api/security")).json()
    assert [e["method"] for e in sec["logins"]] == ["password"]
    assert sec["keys"][0]["name"] == "iPhone" and sec["telegram"]


async def test_telegram_approval_lets_the_device_in_once(env):
    resp = await env.client.post(
        "/api/auth/telegram",
        json={},
        headers={**H, "User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0"},
    )
    asked = await resp.json()
    (msg,) = env.bot.sent
    assert msg["chat_id"] == -100 and asked["code"] in msg["text"]
    assert "Chrome на Windows" in msg["text"]
    poll = f"/api/auth/telegram/{asked['id']}"
    assert (await (await env.client.get(poll)).json())["state"] == "pending"

    data = msg["reply_markup"].inline_keyboard[0][0].callback_data
    answered = []

    async def answer(text=None):
        answered.append(text)

    async def edit(text, **kw):
        answered.append(text)

    query = SimpleNamespace(data=data, answer=answer, edit_message_text=edit)
    await tg_login.on_callback(SimpleNamespace(callback_query=query), None)
    assert answered[0] == "✅ Впустила"

    resp = await env.client.get(poll)
    assert (await resp.json())["state"] == "approved"
    assert (await env.client.get("/api/me")).status == 200
    env.client.session.cookie_jar.clear()
    assert (await (await env.client.get(poll)).json())["state"] == "expired"
    assert (await env.client.get("/api/me")).status == 401


async def test_telegram_requests_are_throttled(env):
    assert (
        await env.client.post("/api/auth/telegram", json={}, headers=H)
    ).status == 200
    resp = await env.client.post("/api/auth/telegram", json={}, headers=H)
    assert resp.status == 429 and (await resp.json())["wait"] > 0
    assert len(env.bot.sent) == 1
