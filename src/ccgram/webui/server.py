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
- ``POST /api/sessions/{tid}/close``      end a web-only session
- ``GET  /api/sessions/{tid}/entries``    structured transcript (``limit``,
  ``before``/``after`` byte cursors)
- ``POST /api/sessions/{tid}/rename``     rename session + its topic
- ``POST /api/sessions/{tid}/upload``     multipart file into the session cwd
- ``POST /api/sessions/{tid}/pin``        pin / unpin in the sidebar
- ``POST /api/sessions/{tid}/sound``      Telegram sound for the topic: auto / on / off
- ``POST /api/sessions/{tid}/detach``     delete the topic, keep the session
- ``POST /api/sessions/{tid}/keep``       keep a session whose topic was deleted
- ``POST /api/sessions/{tid}/delete``     kill the session, delete its topic
- ``POST /api/uploads``                   file for a session not created yet
- ``GET  /api/models``                    Claude models for the picker
- ``GET  /api/events``                    SSE: messages + session-list changes

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
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from . import service
from .auth import COOKIE_NAME, SESSION_TTL_S, LoginThrottle, check_password
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


def _authed(request: web.Request) -> bool:
    return verify_cookie(
        request.cookies.get(COOKIE_NAME),
        request.app[_TOKEN_KEY],
        request.app[_PASSWORD_KEY],
    )


@web.middleware
async def _middleware(
    request: web.Request, handler: Callable[[web.Request], Awaitable[Any]]
) -> Any:
    path = request.path
    if path.startswith("/api/") and path != "/api/login" and not _authed(request):
        return web.json_response({"error": "login required"}, status=401)
    if request.method == "POST" and request.headers.get("X-CCGram") != "1":
        return web.json_response({"error": "missing X-CCGram header"}, status=403)
    try:
        response = await handler(request)
    except service.WebUIError as exc:
        response = web.json_response({"error": str(exc)}, status=exc.status)
    if isinstance(response, web.StreamResponse) and not response.prepared:
        response.headers.update(_SECURITY_HEADERS)
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


async def _login(request: web.Request) -> web.Response:
    throttle = request.app[_THROTTLE_KEY]
    client = _client_ip(request)
    if throttle.blocked(client):
        logger.warning("webui login blocked (throttle) client=%s", client)
        return web.json_response({"error": "too many attempts"}, status=429)
    body = await _json_body(request)
    password = request.app[_PASSWORD_KEY]
    if not check_password(str(body.get("password", "")), password):
        throttle.record_failure(client)
        logger.warning("webui login failed client=%s", client)
        return web.json_response({"error": "wrong password"}, status=401)
    throttle.reset(client)
    logger.info("webui login ok client=%s", client)
    resp = web.json_response({"ok": True})
    resp.set_cookie(
        COOKIE_NAME,
        issue_cookie(request.app[_TOKEN_KEY], password),
        max_age=SESSION_TTL_S,
        httponly=True,
        secure=_is_https(request),
        samesite="Strict",
        path="/",
    )
    return resp


async def _logout(_request: web.Request) -> web.Response:
    resp = web.json_response({"ok": True})
    resp.del_cookie(COOKIE_NAME, path="/")
    return resp


async def _me(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


# ── sessions ─────────────────────────────────────────────────────────────────


async def _sessions(_request: web.Request) -> web.Response:
    return web.json_response({"sessions": await service.list_sessions()})


async def _create(request: web.Request) -> web.Response:
    body = await _json_body(request)
    name = body.get("name") if isinstance(body.get("name"), str) else None
    model = service.valid_model(body.get("model"))
    provider = service.valid_provider(body.get("provider"))
    if body.get("web_only"):
        result = await service.create_web_only_session(
            str(body.get("text", "")), name, model, provider
        )
    else:
        result = await service.create_session(
            request.app[_BOT_KEY],  # type: ignore[arg-type]
            str(body.get("text", "")),
            name,
            model,
            provider,
        )
    return web.json_response(result)


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


async def _sound(request: web.Request) -> web.Response:
    body = await _json_body(request)
    return web.json_response(
        await service.set_sound(_thread_id(request), str(body.get("mode", "")))
    )


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


async def _pane(request: web.Request) -> web.Response:
    return web.json_response(await service.pane(_thread_id(request)))


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
    r.add_post("/api/sessions/{tid}/detach", _detach)
    r.add_post("/api/sessions/{tid}/keep", _keep)
    r.add_post("/api/sessions/{tid}/delete", _delete)
    r.add_post("/api/uploads", _upload_new)
    r.add_get("/api/models", _models)
    r.add_post("/api/sessions/{tid}/key", _key)
    r.add_get("/api/events", _events)
    return app


_runner: web.AppRunner | None = None
_autotitle_task: asyncio.Task[None] | None = None


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
        from .prefs import sound_modes

        quiet_threads.load_modes(sound_modes())
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

        global _autotitle_task
        _autotitle_task = autotitle.start(bot)
    except OSError as exc:
        logger.error("web UI failed to bind: %s", exc)


async def stop_webui() -> None:
    global _runner, _autotitle_task
    if _autotitle_task is not None:
        _autotitle_task.cancel()
        _autotitle_task = None
    if _runner is None:
        return
    with contextlib.suppress(Exception):
        await _runner.cleanup()
    _runner = None
