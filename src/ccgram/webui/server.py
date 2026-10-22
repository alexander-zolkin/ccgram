"""Web UI HTTP server — aiohttp app, JSON API and a server-sent-events stream.

Routes (everything under ``/api`` except login requires the session cookie):
- ``GET  /``                              SPA shell (static)
- ``POST /api/login`` / ``/api/logout``   password login, cookie in/out
- ``GET  /api/me``                        200 when logged in, 401 otherwise
- ``GET  /api/sessions``                  bound topics, newest activity first
- ``POST /api/sessions``                  new topic + quick-start session
- ``GET  /api/sessions/{tid}/messages``   transcript tail (``?limit=``)
- ``POST /api/sessions/{tid}/send``       text into the session (wakes it)
- ``GET  /api/sessions/{tid}/pane``       current pane snapshot
- ``POST /api/sessions/{tid}/key``        one whitelisted key into the pane
- ``POST /api/sessions/{tid}/answer``     allow / deny the prompt a push was for
- ``POST /api/tts``                       one chunk of a reply → MP3 (read aloud)
- ``POST /api/sessions/{tid}/close``      end a web-only session
- ``GET  /api/sessions/{tid}/entries``    structured transcript (``limit``,
  ``before``/``after`` byte cursors)
- ``POST /api/sessions/{tid}/rename``     rename session + its topic
- ``POST /api/sessions/{tid}/upload``     multipart file into the session cwd
- ``POST /api/sessions/{tid}/pin``        pin / unpin in the sidebar
- ``POST /api/sessions/{tid}/sound``      Telegram sound for the topic: auto / on / off
- ``GET  /api/sessions/{tid}/file?path=`` an upload of the session (.ccgram-uploads only)
- ``GET  /api/search?q=``                  full-text search over all transcripts
- ``GET  /api/push/key``, ``POST /api/push/(un)subscribe``  Web Push devices
- ``GET/POST /api/settings``  tg_muted / new_chat_telegram / notify_mode (each|done)
- ``POST /api/sessions/{tid}/probe``       does the Telegram topic still exist?
- ``GET  /api/sessions/{tid}/live``        status line, checklist, subagents, todos
- ``GET/POST /api/sessions/{tid}/mode``    Claude Code mode (POST = Shift+Tab)
- ``GET  /api/sessions/{tid}/terminal``    pane with ANSI colours
- ``GET  /api/commands?tid=|provider=``    slash commands + skills
- ``POST /api/stt``                         voice → text (Whisper)
- ``GET  /api/browse?path=``, ``/api/resumable?cwd=``  folders / old sessions
- ``POST /api/presence``                   "this tab is visible" (mutes push)
- ``POST /api/sessions/{tid}/detach``     delete the topic, keep the session
- ``POST /api/sessions/{tid}/keep``       keep a session whose topic was deleted
- ``POST /api/sessions/{tid}/delete``     kill the session, delete its topic
- ``POST /api/uploads``                   file for a session not created yet
- ``GET  /api/models``                    Claude models for the picker
- ``GET  /api/events``                    SSE: messages + session-list changes
- ``GET  /api/auth/methods``              which logins are open (no session needed)
- ``POST /api/auth/passkey/login/options``, ``/api/auth/passkey/login``  passkey login
- ``POST /api/auth/telegram``, ``GET /api/auth/telegram/{id}``  approve in Telegram
- ``POST /api/auth/passkeys``, ``/api/auth/passkeys/options|{id}/delete``  passkeys
- ``GET  /api/security``, ``POST /api/security/password|sign-out-others``
- ``GET  /api/hub``                       home page: now / reach / pending / memory
- ``POST /api/hub/reach``                 probe every reach target now
- ``POST /api/hub/pending/{id}/close|snooze``  close (to the done log) / postpone
- ``GET  /api/hub/memory/search?q=``, ``/api/hub/memory/file?path=``
- ``POST /api/hub/memory/save|forget``     edit a memory / move it to the archive
- ``GET/POST /api/hub/diary``              a day's digest (POST writes it)
- ``GET  /hub/map/…``                     memory map site for the home-page iframe

State-changing requests must carry ``X-CCGram: 1``; with no CORS headers
served, a foreign page can't attach it, which together with the SameSite
cookie closes off cross-site request forgery.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from . import extras, hub, passkeys, push, security, service, tg_login, tts
from ..providers.claude import EFFORT_LEVELS
from .auth import COOKIE_NAME, SESSION_TTL_S, LoginThrottle
from .auth import issue_cookie, verify_cookie
from .bus import bus

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telegram import Bot

logger = logging.getLogger(__name__)

# CCGRAM_WEBUI_STATIC_DIR lets the front-end be updated without reinstalling
# (and restarting) the daemon; it falls back to the packaged copy.
_STATIC_DIR = Path(
    os.environ.get("CCGRAM_WEBUI_STATIC_DIR") or Path(__file__).parent / "static"
)

_BOT_KEY = web.AppKey("webui_bot", object)
_TOKEN_KEY = web.AppKey("webui_bot_token", str)
MAX_AUDIO = 20 * 1024 * 1024
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_PASSWORD_KEY = web.AppKey("webui_password", str)
_THROTTLE_KEY = web.AppKey("webui_throttle", LoginThrottle)

SESSIONS_POLL_S = 3.0
HEARTBEAT_S = 15.0

_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline';"
        " frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _client_ip(request: web.Request) -> str:
    """Peer address; behind a loopback proxy, the hop the proxy appended."""
    peer = request.remote or ""
    try:
        loopback = ip_address(peer).is_loopback
    except ValueError:
        loopback = False
    forwarded = request.headers.get("X-Forwarded-For", "")
    if loopback and forwarded:
        return forwarded.split(",")[-1].strip()
    return peer


def _is_https(request: web.Request) -> bool:
    return request.secure or request.headers.get("X-Forwarded-Proto") == "https"


# Login endpoints, reachable without a session.
_PUBLIC_API = {
    "/api/login",
    "/api/auth/methods",
    "/api/auth/passkey/login/options",
    "/api/auth/passkey/login",
    "/api/auth/telegram",
}


def _public_api(path: str) -> bool:
    return path in _PUBLIC_API or path.startswith("/api/auth/telegram/")


def _relying_party(request: web.Request) -> tuple[str, str]:
    """(rp id, origin) of the page: the host Caddy forwarded, scheme included."""
    host = request.host
    scheme = "https" if _is_https(request) else "http"
    return host.split(":")[0], f"{scheme}://{host}"


def _authed(request: web.Request) -> bool:
    return verify_cookie(
        request.cookies.get(COOKIE_NAME),
        request.app[_TOKEN_KEY],
        security.cookie_material(request.app[_PASSWORD_KEY]),
    )


@web.middleware
async def _middleware(
    request: web.Request, handler: Callable[[web.Request], Awaitable[Any]]
) -> Any:
    path = request.path
    if path.startswith("/api/") and not _public_api(path) and not _authed(request):
        return web.json_response({"error": "login required"}, status=401)
    if request.method == "POST" and request.headers.get("X-CCGram") != "1":
        return web.json_response({"error": "missing X-CCGram header"}, status=403)
    try:
        response = await handler(request)
    except service.WebUIError as exc:
        response = web.json_response({"error": str(exc)}, status=exc.status)
    if isinstance(response, web.StreamResponse) and not response.prepared:
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
    return response


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError, UnicodeDecodeError:
        raise service.WebUIError("invalid JSON") from None
    if not isinstance(body, dict):
        raise service.WebUIError("invalid JSON")
    return body


def _thread_id(request: web.Request) -> int:
    try:
        return int(request.match_info["tid"])
    except ValueError:
        raise service.WebUIError("bad session id") from None


# ── pages ────────────────────────────────────────────────────────────────────


async def _index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(_STATIC_DIR / "index.html")


async def _manifest(_request: web.Request) -> web.FileResponse:
    resp = web.FileResponse(_STATIC_DIR / "manifest.webmanifest")
    resp.content_type = "application/manifest+json"
    return resp


async def _service_worker(_request: web.Request) -> web.FileResponse:
    resp = web.FileResponse(_STATIC_DIR / "sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


# ── auth ─────────────────────────────────────────────────────────────────────


def _with_session(request: web.Request, payload: dict[str, Any]) -> web.Response:
    resp = web.json_response(payload)
    resp.set_cookie(
        COOKIE_NAME,
        issue_cookie(
            request.app[_TOKEN_KEY],
            security.cookie_material(request.app[_PASSWORD_KEY]),
        ),
        max_age=SESSION_TTL_S,
        httponly=True,
        secure=_is_https(request),
        samesite="Strict",
        path="/",
    )
    return resp


def _device(request: web.Request) -> str:
    return tg_login.device_label(request.headers.get("User-Agent", ""))


async def _login(request: web.Request) -> web.Response:
    throttle = request.app[_THROTTLE_KEY]
    client = _client_ip(request)
    if throttle.blocked(client):
        logger.warning("webui login blocked (throttle) client=%s", client)
        return web.json_response({"error": "too many attempts"}, status=429)
    body = await _json_body(request)
    ok = security.check_password(
        str(body.get("password", "")), request.app[_PASSWORD_KEY]
    )
    security.record("password", ok, _device(request), client)
    if not ok:
        throttle.record_failure(client)
        logger.warning("webui login failed client=%s", client)
        return web.json_response({"error": "wrong password"}, status=401)
    throttle.reset(client)
    logger.info("webui login ok client=%s", client)
    return _with_session(request, {"ok": True})


# ── passkeys + Telegram approval ─────────────────────────────────────────────


async def _auth_methods(_request: web.Request) -> web.Response:
    return web.json_response(
        {
            "password": True,
            "passkey": passkeys.count() > 0,
            "telegram": tg_login.enabled(),
        }
    )


async def _passkey_login_options(request: web.Request) -> web.Response:
    rp_id, _origin = _relying_party(request)
    return web.json_response(passkeys.login_options(rp_id))


async def _passkey_login(request: web.Request) -> web.Response:
    throttle = request.app[_THROTTLE_KEY]
    client = _client_ip(request)
    if throttle.blocked(client):
        return web.json_response({"error": "too many attempts"}, status=429)
    body = await _json_body(request)
    rp_id, origin = _relying_party(request)
    credential = body.get("credential")
    if not isinstance(credential, dict):
        raise service.WebUIError("no credential")
    try:
        key = passkeys.login(rp_id, origin, str(body.get("token", "")), credential)
    except passkeys.PasskeyError as exc:
        throttle.record_failure(client)
        security.record("passkey", False, _device(request), client)
        logger.warning("webui passkey login failed client=%s: %s", client, exc)
        return web.json_response({"error": str(exc)}, status=401)
    throttle.reset(client)
    security.record("passkey", True, key["name"], client)
    logger.info("webui passkey login ok client=%s key=%s", client, key["name"])
    return _with_session(request, {"ok": True, "key": key})


async def _telegram_login(request: web.Request) -> web.Response:
    if not tg_login.enabled():
        raise service.WebUIError("Telegram login is not available", 404)
    try:
        asked = await tg_login.start(
            request.app[_BOT_KEY],
            _client_ip(request),
            request.headers.get("User-Agent", ""),
        )
    except tg_login.TooSoonError as exc:
        return web.json_response(
            {"error": "too many requests", "wait": exc.wait}, status=429
        )
    return web.json_response(asked)


async def _telegram_login_poll(request: web.Request) -> web.Response:
    state = tg_login.poll(request.match_info["id"])
    if state == "approved":
        security.record("telegram", True, _device(request), _client_ip(request))
        logger.info("webui telegram login ok client=%s", _client_ip(request))
        return _with_session(request, {"state": state})
    return web.json_response({"state": state})


async def _security(_request: web.Request) -> web.Response:
    return web.json_response(
        {
            **security.overview(),
            "keys": passkeys.credentials(),
            "telegram": tg_login.enabled(),
        }
    )


async def _security_password(request: web.Request) -> web.Response:
    """New password; proven by the current one or a fresh passkey touch."""
    body = await _json_body(request)
    throttle = request.app[_THROTTLE_KEY]
    client = _client_ip(request)
    if throttle.blocked(client):
        return web.json_response({"error": "too many attempts"}, status=429)
    proof = body.get("passkey")
    if isinstance(proof, dict) and isinstance(proof.get("credential"), dict):
        rp_id, origin = _relying_party(request)
        try:
            passkeys.login(
                rp_id, origin, str(proof.get("token", "")), proof["credential"]
            )
        except passkeys.PasskeyError as exc:
            throttle.record_failure(client)
            raise service.WebUIError(str(exc), 401) from None
    elif not security.check_password(
        str(body.get("current", "")), request.app[_PASSWORD_KEY]
    ):
        throttle.record_failure(client)
        raise service.WebUIError("wrong password", 401)
    try:
        security.set_password(str(body.get("new", "")))
    except ValueError as exc:
        raise service.WebUIError(str(exc)) from None
    logger.info("webui password changed client=%s", client)
    # The secret rotated: every other browser is out, this one gets a fresh cookie.
    return _with_session(request, {"ok": True, **security.overview()})


async def _security_sign_out_others(request: web.Request) -> web.Response:
    security.sign_out_everywhere()
    logger.info("webui signed out everywhere else client=%s", _client_ip(request))
    return _with_session(request, {"ok": True})


async def _passkey_options(request: web.Request) -> web.Response:
    rp_id, _origin = _relying_party(request)
    return web.json_response(passkeys.registration_options(rp_id))


async def _passkey_add(request: web.Request) -> web.Response:
    body = await _json_body(request)
    rp_id, origin = _relying_party(request)
    credential = body.get("credential")
    if not isinstance(credential, dict):
        raise service.WebUIError("no credential")
    name = str(
        body.get("name") or tg_login.device_label(request.headers.get("User-Agent", ""))
    )
    try:
        key = passkeys.register(
            rp_id, origin, str(body.get("token", "")), credential, name
        )
    except passkeys.PasskeyError as exc:
        raise service.WebUIError(str(exc)) from None
    logger.info("webui passkey added: %s", key["name"])
    return web.json_response({"key": key, "keys": passkeys.credentials()})


async def _passkey_delete(request: web.Request) -> web.Response:
    if not passkeys.remove(request.match_info["id"]):
        raise service.WebUIError("no such key", 404)
    return web.json_response({"keys": passkeys.credentials()})


async def _logout(_request: web.Request) -> web.Response:
    resp = web.json_response({"ok": True})
    resp.del_cookie(COOKIE_NAME, path="/")
    return resp


async def _me(_request: web.Request) -> web.Response:
    # "hub": the home page has cards, so the client lays it out before /api/hub.
    return web.json_response(
        {"ok": True, "hub": bool(hub.config()), "tts": tts.enabled()}
    )


# ── sessions ─────────────────────────────────────────────────────────────────


async def _sessions(_request: web.Request) -> web.Response:
    return web.json_response({"sessions": await service.list_sessions()})


async def _create(request: web.Request) -> web.Response:
    return web.json_response(await _launch(request, await _json_body(request)))


async def _launch(request: web.Request, body: dict[str, Any]) -> dict[str, Any]:
    """Start a session as the new-chat composer asks (``POST /api/sessions``)."""
    name = body.get("name") if isinstance(body.get("name"), str) else None
    model = service.valid_model(body.get("model"))
    provider = service.valid_provider(body.get("provider"))
    effort = body.get("effort") if body.get("effort") in EFFORT_LEVELS else None
    cwd = await asyncio.to_thread(extras.valid_cwd, body.get("cwd") or None)
    resume_id = body.get("resume_id") or None
    if resume_id is not None and not _UUID_RE.match(str(resume_id)):
        raise service.WebUIError("bad session id")
    if body.get("worktree") and cwd:
        title = name or str(body.get("text", ""))[:40]
        cwd = await extras.make_worktree(cwd, title)
    logger.info(
        "webui create: web_only=%s provider=%s model=%s effort=%s cwd=%s resume=%s",
        bool(body.get("web_only")),
        provider,
        model,
        effort,
        cwd,
        resume_id,
    )
    if body.get("web_only"):
        return await service.create_web_only_session(
            str(body.get("text", "")), name, model, provider, effort, cwd, resume_id
        )
    return await service.create_session(
        request.app[_BOT_KEY],  # type: ignore[arg-type]
        str(body.get("text", "")),
        name,
        model,
        provider,
        effort,
        cwd,
        resume_id,
    )


async def _messages(request: web.Request) -> web.Response:
    try:
        limit = int(request.query.get("limit", service.DEFAULT_HISTORY))
    except ValueError:
        limit = service.DEFAULT_HISTORY
    return web.json_response(await service.history(_thread_id(request), limit))


async def _send(request: web.Request) -> web.Response:
    body = await _json_body(request)
    result = await service.send_text(
        request.app[_BOT_KEY],  # type: ignore[arg-type]
        _thread_id(request),
        str(body.get("text", "")),
    )
    return web.json_response(result)


async def _entries(request: web.Request) -> web.Response:
    def _int(name: str) -> int | None:
        raw = request.query.get(name)
        try:
            return int(raw) if raw is not None else None
        except ValueError:
            raise service.WebUIError(f"bad {name}") from None

    limit = _int("limit") or service.DEFAULT_HISTORY
    return web.json_response(
        await service.entries(
            _thread_id(request), limit=limit, before=_int("before"), after=_int("after")
        )
    )


async def _rename(request: web.Request) -> web.Response:
    body = await _json_body(request)
    return web.json_response(
        await service.rename(
            request.app[_BOT_KEY],  # type: ignore[arg-type]
            _thread_id(request),
            str(body.get("name", "")),
        )
    )


async def _upload(request: web.Request) -> web.Response:
    return await _upload_into(request, _thread_id(request))


async def _upload_into(request: web.Request, tid: int | None) -> web.Response:
    reader = await request.multipart()
    part = await reader.next()
    if part is None or getattr(part, "name", None) != "file":
        raise service.WebUIError("no file")
    data = await part.read(decode=False)  # type: ignore[union-attr]
    filename = getattr(part, "filename", None) or "file"
    result = await asyncio.to_thread(service.save_upload, tid, filename, bytes(data))
    return web.json_response(result)


async def _upload_new(request: web.Request) -> web.Response:
    return await _upload_into(request, None)


async def _models(_request: web.Request) -> web.Response:
    return web.json_response(await service.list_models())


async def _pin(request: web.Request) -> web.Response:
    body = await _json_body(request)
    return web.json_response(
        await service.set_pin(_thread_id(request), bool(body.get("pinned")))
    )


async def _settings(request: web.Request) -> web.Response:
    # Lazy: leaf modules.
    from .. import quiet_threads
    from .prefs import new_chat_telegram, set_new_chat_telegram, set_tg_muted
    from .prefs import set_notify_mode as save_notify_mode
    from .prefs import set_web_muted as save_web_muted

    if request.method == "POST":
        body = await _json_body(request)
        if "tg_muted" in body:
            quiet_threads.set_all_muted(bool(body["tg_muted"]))
            set_tg_muted(bool(body["tg_muted"]))
        if "new_chat_telegram" in body:
            # Server-side on purpose: an iOS home-screen app has its own
            # localStorage, so a browser-only choice silently reset there.
            set_new_chat_telegram(bool(body["new_chat_telegram"]))
        if "web_muted" in body:
            push.set_web_muted(bool(body["web_muted"]))
            save_web_muted(bool(body["web_muted"]))
        if body.get("notify_mode") in extras.NOTIFY_MODES:
            extras.set_notify_mode(body["notify_mode"])
            save_notify_mode(body["notify_mode"])
    return web.json_response(
        {
            "tg_muted": quiet_threads.all_muted(),
            "new_chat_telegram": new_chat_telegram(),
            "notify_mode": extras.notify_mode(),
            "web_muted": push.web_muted(),
            "web_off": push.web_off(),
            "stt": bool(os.getenv("CCGRAM_WHISPER_PROVIDER")),
        }
    )


async def _hub(_request: web.Request) -> web.Response:
    return web.json_response(await hub.overview())


async def _hub_reach(_request: web.Request) -> web.Response:
    return web.json_response(await hub.reach(refresh=True))


async def _hub_pending(request: web.Request) -> web.Response:
    item, action = request.match_info["id"], request.match_info["action"]
    if action == "close":
        return web.json_response(await asyncio.to_thread(hub.close_pending, item))
    body = await _json_body(request)
    if action == "start":
        # A chat for the item, set up like the composer's (Telegram or not,
        # model, effort); its first message carries the item's context.
        brief = await asyncio.to_thread(hub.task_brief, item)
        launch = {k: body.get(k) for k in ("web_only", "provider", "model", "effort")}
        result = await _launch(request, {**launch, "text": brief["text"]})
        await asyncio.to_thread(hub.link_task, brief["key"], result["thread_id"])
        return web.json_response({**result, "text": brief["text"]})
    days = body.get("days")
    if action != "snooze" or not isinstance(days, int):
        raise service.WebUIError("unknown action")
    return web.json_response(await asyncio.to_thread(hub.snooze_pending, item, days))


# The memory map is a generated single-page site with inline scripts; it may
# only be framed by this origin's home page.
_MAP_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline';"
    " style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:;"
    " frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
)


async def _hub_map(request: web.Request) -> web.StreamResponse:
    if not _authed(request):
        return web.Response(status=401, text="login required")
    path = await asyncio.to_thread(hub.map_file, request.match_info["tail"])
    response = web.FileResponse(path)
    response.headers["Content-Security-Policy"] = _MAP_CSP
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Cache-Control"] = "no-cache"
    return response


async def _hub_memory_search(request: web.Request) -> web.Response:
    query = request.query.get("q", "")[:200]
    return web.json_response(await asyncio.to_thread(hub.memory_search, query))


async def _hub_memory_save(request: web.Request) -> web.Response:
    body = await _json_body(request)
    description = body.get("description")
    return web.json_response(
        await asyncio.to_thread(
            hub.memory_save,
            str(body.get("path", "")),
            str(body.get("body", "")),
            str(description) if description is not None else None,
            float(body.get("mtime") or 0),
        )
    )


async def _hub_memory_forget(request: web.Request) -> web.Response:
    body = await _json_body(request)
    return web.json_response(
        await asyncio.to_thread(hub.memory_forget, str(body.get("path", "")))
    )


async def _hub_diary(request: web.Request) -> web.Response:
    if request.method == "POST":
        day = str((await _json_body(request)).get("date", ""))
        return web.json_response(await hub.diary_make(day))
    return web.json_response(hub.diary_get(request.query.get("date", "")))


async def _hub_memory_file(request: web.Request) -> web.Response:
    rel = request.query.get("path", "")
    return web.json_response(await asyncio.to_thread(hub.memory_file, rel))


async def _probe(request: web.Request) -> web.Response:
    return web.json_response(
        await extras.probe_topic(request.app[_BOT_KEY], _thread_id(request))
    )


async def _live(request: web.Request) -> web.Response:
    return web.json_response(await extras.live(_thread_id(request)))


async def _mode(request: web.Request) -> web.Response:
    """GET = read; POST ``{}`` = one Shift+Tab; POST ``{"target": "Plan"}`` = go there."""
    tid = _thread_id(request)
    if request.method != "POST":
        return web.json_response(await extras.approval_mode(tid))
    target = (await _json_body(request)).get("target")
    if target is not None and not isinstance(target, str):
        raise service.WebUIError("unknown mode")
    return web.json_response(
        await extras.approval_mode(tid, cycle=target is None, target=target)
    )


async def _terminal(request: web.Request) -> web.Response:
    return web.json_response(await extras.terminal(_thread_id(request)))


async def _commands(request: web.Request) -> web.Response:
    tid = request.query.get("tid")
    result = await asyncio.to_thread(
        extras.commands,
        int(tid) if tid and tid.lstrip("-").isdigit() else None,
        request.query.get("provider", "claude"),
    )
    return web.json_response(result)


async def _stt(request: web.Request) -> web.Response:
    reader = await request.multipart()
    part = await reader.next()
    if part is None or getattr(part, "name", None) != "audio":
        raise service.WebUIError("no audio")
    data = await part.read(decode=False)  # type: ignore[union-attr]
    if len(data) > MAX_AUDIO:
        raise service.WebUIError("recording too long", 413)
    filename = getattr(part, "filename", None) or "voice.webm"
    return web.json_response(await extras.transcribe(bytes(data), filename))


async def _browse(request: web.Request) -> web.Response:
    return web.json_response(
        await asyncio.to_thread(extras.browse, request.query.get("path"))
    )


async def _resumable(request: web.Request) -> web.Response:
    return web.json_response(await extras.resumable(request.query.get("cwd")))


async def _push_key(_request: web.Request) -> web.Response:
    return web.json_response({"key": push.public_key()})


async def _push_subscribe(request: web.Request) -> web.Response:
    body = await _json_body(request)
    try:
        count = push.subscribe(body.get("subscription"))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"ok": True, "devices": count})


async def _push_unsubscribe(request: web.Request) -> web.Response:
    body = await _json_body(request)
    push.unsubscribe(str(body.get("endpoint", "")))
    return web.json_response({"ok": True})


async def _presence(request: web.Request) -> web.Response:
    body = await _json_body(request)
    push.mark_visible(str(body.get("client", "")), bool(body.get("visible")))
    return web.json_response({"ok": True})


async def _sound(request: web.Request) -> web.Response:
    """Per-chat notifications: ``mode`` = Telegram sound, ``web`` = web on/off."""
    # Lazy: prefs is a leaf.
    from .prefs import set_web_off

    body = await _json_body(request)
    tid = _thread_id(request)
    result: dict[str, Any] = {"ok": True}
    if "web" in body:
        service.find_binding(tid)
        push.set_chat_web(tid, bool(body["web"]))
        set_web_off(push.web_off())
        result["web"] = tid not in push.web_off()
    if "mode" in body:
        result.update(await service.set_sound(tid, str(body.get("mode", ""))))
    return web.json_response(result)


async def _keep(request: web.Request) -> web.Response:
    return web.json_response(await service.keep_web_only(_thread_id(request)))


async def _detach(request: web.Request) -> web.Response:
    return web.json_response(
        await service.remove_from_telegram(
            request.app[_BOT_KEY],  # type: ignore[arg-type]
            _thread_id(request),
        )
    )


async def _delete(request: web.Request) -> web.Response:
    return web.json_response(
        await service.delete_session(
            request.app[_BOT_KEY],  # type: ignore[arg-type]
            _thread_id(request),
        )
    )


async def _close(request: web.Request) -> web.Response:
    return web.json_response(await service.close_web_only(_thread_id(request)))


async def _search(request: web.Request) -> web.Response:
    return web.json_response(await service.search(request.query.get("q", "")))


async def _file(request: web.Request) -> web.StreamResponse:
    path = service.upload_file(_thread_id(request), request.query.get("path", ""))
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


async def _pane(request: web.Request) -> web.Response:
    return web.json_response(await service.pane(_thread_id(request)))


async def _answer(request: web.Request) -> web.Response:
    """«Разрешить» / «Отклонить» from a notification: only for the same prompt."""
    body = await _json_body(request)
    return web.json_response(
        await extras.answer_prompt(
            _thread_id(request),
            str(body.get("prompt", "")),
            str(body.get("choice", "")),
        )
    )


async def _tts(request: web.Request) -> web.Response:
    body = await _json_body(request)
    audio = await tts.speak(str(body.get("text", "")))
    return web.Response(
        body=audio,
        content_type="audio/mpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


async def _key(request: web.Request) -> web.Response:
    body = await _json_body(request)
    return web.json_response(
        await service.send_key(_thread_id(request), str(body.get("key", "")))
    )


# ── live stream ──────────────────────────────────────────────────────────────


def _sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


async def _events(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            **_SECURITY_HEADERS,
        }
    )
    await resp.prepare(request)
    queue = bus.subscribe()
    loop = asyncio.get_running_loop()
    last_sessions: list[dict[str, Any]] | None = None
    next_poll = 0.0
    last_write = loop.time()
    try:
        while True:
            now = loop.time()
            if now >= next_poll:
                sessions = await service.list_sessions()
                if sessions != last_sessions:
                    await resp.write(_sse({"type": "sessions", "sessions": sessions}))
                    last_sessions = sessions
                    last_write = now
                next_poll = now + SESSIONS_POLL_S
            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=max(0.1, next_poll - loop.time())
                )
            except TimeoutError:
                if loop.time() - last_write >= HEARTBEAT_S:
                    await resp.write(b": ping\n\n")
                    last_write = loop.time()
                continue
            await resp.write(_sse(event))
            last_write = loop.time()
    except ConnectionResetError, asyncio.CancelledError:
        pass
    finally:
        bus.unsubscribe(queue)
    return resp


# ── app lifecycle ────────────────────────────────────────────────────────────


def build_app(*, bot: Bot | Any, bot_token: str, password: str) -> web.Application:
    app = web.Application(
        middlewares=[_middleware], client_max_size=service.MAX_UPLOAD + 1024 * 1024
    )
    app[_BOT_KEY] = bot
    app[_TOKEN_KEY] = bot_token
    app[_PASSWORD_KEY] = password
    app[_THROTTLE_KEY] = LoginThrottle()
    r = app.router
    r.add_get("/", _index)
    r.add_get("/manifest.webmanifest", _manifest)
    r.add_get("/sw.js", _service_worker)
    r.add_static("/static/", path=_STATIC_DIR, show_index=False)
    r.add_post("/api/login", _login)
    r.add_post("/api/logout", _logout)
    r.add_get("/api/me", _me)
    r.add_get("/api/sessions", _sessions)
    r.add_post("/api/sessions", _create)
    r.add_get("/api/sessions/{tid}/messages", _messages)
    r.add_post("/api/sessions/{tid}/send", _send)
    r.add_get("/api/sessions/{tid}/pane", _pane)
    r.add_post("/api/sessions/{tid}/close", _close)
    r.add_get("/api/sessions/{tid}/entries", _entries)
    r.add_post("/api/sessions/{tid}/rename", _rename)
    r.add_post("/api/sessions/{tid}/upload", _upload)
    r.add_post("/api/sessions/{tid}/pin", _pin)
    r.add_post("/api/sessions/{tid}/sound", _sound)
    r.add_get("/api/sessions/{tid}/file", _file)
    r.add_get("/api/search", _search)
    r.add_post("/api/sessions/{tid}/probe", _probe)
    r.add_get("/api/sessions/{tid}/live", _live)
    r.add_get("/api/sessions/{tid}/mode", _mode)
    r.add_post("/api/sessions/{tid}/mode", _mode)
    r.add_get("/api/sessions/{tid}/terminal", _terminal)
    r.add_get("/api/commands", _commands)
    r.add_post("/api/stt", _stt)
    r.add_get("/api/browse", _browse)
    r.add_get("/api/resumable", _resumable)
    r.add_get("/api/settings", _settings)
    r.add_post("/api/settings", _settings)
    r.add_get("/api/push/key", _push_key)
    r.add_post("/api/push/subscribe", _push_subscribe)
    r.add_post("/api/push/unsubscribe", _push_unsubscribe)
    r.add_post("/api/presence", _presence)
    r.add_post("/api/sessions/{tid}/detach", _detach)
    r.add_post("/api/sessions/{tid}/keep", _keep)
    r.add_post("/api/sessions/{tid}/delete", _delete)
    r.add_post("/api/uploads", _upload_new)
    r.add_get("/api/models", _models)
    r.add_post("/api/sessions/{tid}/key", _key)
    r.add_post("/api/sessions/{tid}/answer", _answer)
    r.add_post("/api/tts", _tts)
    r.add_get("/api/events", _events)
    _add_auth_routes(r)
    _add_hub_routes(r)
    return app


def _add_auth_routes(r: web.UrlDispatcher) -> None:
    r.add_get("/api/auth/methods", _auth_methods)
    r.add_post("/api/auth/passkey/login/options", _passkey_login_options)
    r.add_post("/api/auth/passkey/login", _passkey_login)
    r.add_post("/api/auth/telegram", _telegram_login)
    r.add_get("/api/auth/telegram/{id}", _telegram_login_poll)
    r.add_post("/api/auth/passkeys/options", _passkey_options)
    r.add_post("/api/auth/passkeys", _passkey_add)
    r.add_post("/api/auth/passkeys/{id}/delete", _passkey_delete)
    r.add_get("/api/security", _security)
    r.add_post("/api/security/password", _security_password)
    r.add_post("/api/security/sign-out-others", _security_sign_out_others)


def _add_hub_routes(r: web.UrlDispatcher) -> None:
    r.add_get("/api/hub", _hub)
    r.add_post("/api/hub/reach", _hub_reach)
    r.add_post("/api/hub/pending/{id}/{action}", _hub_pending)
    r.add_get("/api/hub/memory/search", _hub_memory_search)
    r.add_get("/api/hub/memory/file", _hub_memory_file)
    r.add_post("/api/hub/memory/save", _hub_memory_save)
    r.add_post("/api/hub/memory/forget", _hub_memory_forget)
    r.add_get("/api/hub/diary", _hub_diary)
    r.add_post("/api/hub/diary", _hub_diary)
    r.add_get("/hub/map/{tail:.*}", _hub_map)


_runner: web.AppRunner | None = None
_autotitle_task: asyncio.Task[None] | None = None
_hub_task: asyncio.Task[None] | None = None


async def start_webui(bot: Bot | Any) -> None:
    """Start the web UI when port + password are configured. Never raises."""
    global _runner
    # Lazy: config singleton reads env at import; keep module import cheap.
    from ..config import config

    if _runner is not None or not config.webui_port:
        return
    if not config.webui_password:
        logger.warning(
            "CCGRAM_WEBUI_PORT set without CCGRAM_WEBUI_PASSWORD; web UI off"
        )
        return
    try:
        # Lazy: leaf modules, only needed once the UI is actually enabled.
        from .. import quiet_threads
        from .prefs import notify_mode, sound_modes, tg_muted
        from .prefs import web_muted as prefs_web_muted
        from .prefs import web_off as prefs_web_off

        quiet_threads.load_modes(sound_modes())
        quiet_threads.set_all_muted(tg_muted())
        extras.set_notify_mode(notify_mode())
        push.set_web_muted(prefs_web_muted())
        push.load_web_off(prefs_web_off())
        app = build_app(
            bot=bot,
            bot_token=config.telegram_bot_token,
            password=config.webui_password,
        )
        runner = web.AppRunner(app, shutdown_timeout=2.0)
        await runner.setup()
        await web.TCPSite(
            runner, host=config.webui_host, port=config.webui_port
        ).start()
        _runner = runner
        logger.info("web UI listening on %s:%d", config.webui_host, config.webui_port)
        # Lazy: only the running daemon names sessions.
        from . import autotitle

        global _autotitle_task, _hub_task
        _autotitle_task = autotitle.start(bot)
        _hub_task = hub.start()
    except OSError as exc:
        logger.error("web UI failed to bind: %s", exc)


async def stop_webui() -> None:
    global _runner, _autotitle_task, _hub_task
    for task in (_autotitle_task, _hub_task):
        if task is not None:
            task.cancel()
    _autotitle_task = _hub_task = None
    if _runner is None:
        return
    with contextlib.suppress(Exception):
        await _runner.cleanup()
    _runner = None
