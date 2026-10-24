"""Kara Hub's Telegram module: the few routes the hub drives Telegram through.

CCGRAM-HOTFIX:hub-api. The browser UI that used to live here moved to Kara
Hub (its own service); what stays is the small local API the hub calls for
Telegram-topic work, with its token (``<ccgram dir>/hub_token``, made on the
first start, 0600, sent as ``X-Kara-Hub-Token``):

- ``POST /api/sessions``                  new topic + quick-start session
  (a repeated ``key`` returns the same session)
- ``POST /api/sessions/{tid}/send``       text into the session (wakes it)
- ``POST /api/sessions/{tid}/rename``     rename the session + its topic
- ``POST /api/sessions/{tid}/sound``      Telegram sound for the topic: auto / on / off
- ``POST /api/sessions/{tid}/probe``      does the Telegram topic still exist?
- ``POST /api/sessions/{tid}/detach``     delete the topic, keep the session
- ``POST /api/sessions/{tid}/keep``       keep a session whose topic was deleted
- ``POST /api/sessions/{tid}/delete``     kill the session, delete its topic
- ``GET/POST /api/settings``              tg_muted / tg_relay / new_chat_telegram /
  notify_mode (each|done)

POSTs must also carry ``X-CCGram: 1``. Binds loopback only.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import os
import re
import secrets
from typing import TYPE_CHECKING, Any

from aiohttp import web

from . import extras, service
from ..providers.claude import EFFORT_LEVELS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telegram import Bot

logger = logging.getLogger(__name__)

_BOT_KEY = web.AppKey("webui_bot", object)
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# JSON bodies only (a first message is at most service.MAX_TEXT_LEN).
_MAX_BODY = 1024 * 1024

_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

HUB_TOKEN_HEADER = "X-Kara-Hub-Token"
_HUB_ROUTES = re.compile(
    r"^(?:POST /api/sessions"
    r"|POST /api/sessions/-?\d{1,12}/(?:send|rename|sound|probe|detach|keep|delete)"
    r"|(?:GET|POST) /api/settings)$"
)


def _hub_token() -> str:
    from ..utils import ccgram_dir

    path = ccgram_dir() / "hub_token"
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if not token:
        token = secrets.token_urlsafe(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
    return token


def _hub_authed(request: web.Request) -> bool:
    given = request.headers.get(HUB_TOKEN_HEADER)
    if not given or not _HUB_ROUTES.match(f"{request.method} {request.path}"):
        return False
    return hmac.compare_digest(given.encode(), _hub_token().encode())


@web.middleware
async def _middleware(
    request: web.Request, handler: Callable[[web.Request], Awaitable[Any]]
) -> Any:
    if not _hub_authed(request):
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


# ── sessions ─────────────────────────────────────────────────────────────────

# The hub sends a one-time ``key`` with each create. A request dropped while
# the server carries on is asked again with the same key: it gets the same
# chat, not a second one.
_CREATE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_CREATE_KEY_TTL_S = 600.0
_creates: dict[str, tuple[float, asyncio.Task[dict[str, Any]]]] = {}


async def _create(request: web.Request) -> web.Response:
    body = await _json_body(request)
    key = body.get("key")
    if not isinstance(key, str) or not _CREATE_KEY_RE.match(key):
        return web.json_response(await _launch(request, body))
    now = asyncio.get_running_loop().time()
    for old, (at, _task) in list(_creates.items()):
        if now - at > _CREATE_KEY_TTL_S:
            del _creates[old]
    if key in _creates:
        task = _creates[key][1]
    else:
        task = asyncio.get_running_loop().create_task(_launch(request, body))
        _creates[key] = (now, task)

        def _forget_failed(done: asyncio.Task[dict[str, Any]]) -> None:
            # A failed create may be retried under its key; a finished one stays.
            if done.cancelled() or done.exception() is not None:
                _creates.pop(key, None)

        task.add_done_callback(_forget_failed)
    # Shielded: a dropped connection must not cancel a launch half-way.
    return web.json_response(await asyncio.shield(task))


async def _launch(request: web.Request, body: dict[str, Any]) -> dict[str, Any]:
    """Start a session with its own Telegram topic (``POST /api/sessions``)."""
    if body.get("web_only") or body.get("device") not in (None, "", "local"):
        # Chats without a topic, and other machines, are the hub's own (kara-node).
        raise service.WebUIError("only chats with a Telegram topic are made here")
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
        "webui create: provider=%s model=%s effort=%s cwd=%s resume=%s",
        provider,
        model,
        effort,
        cwd,
        resume_id,
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


async def _send(request: web.Request) -> web.Response:
    body = await _json_body(request)
    result = await service.send_text(
        request.app[_BOT_KEY],  # type: ignore[arg-type]
        _thread_id(request),
        str(body.get("text", "")),
    )
    return web.json_response(result)


async def _rename(request: web.Request) -> web.Response:
    body = await _json_body(request)
    return web.json_response(
        await service.rename(
            request.app[_BOT_KEY],  # type: ignore[arg-type]
            _thread_id(request),
            str(body.get("name", "")),
        )
    )


async def _settings(request: web.Request) -> web.Response:
    # Lazy: leaf modules.
    from .. import quiet_threads
    from .prefs import new_chat_telegram, set_new_chat_telegram, set_tg_muted
    from .prefs import set_notify_mode as save_notify_mode
    from .prefs import set_tg_relay

    if request.method == "POST":
        body = await _json_body(request)
        if "tg_muted" in body:
            quiet_threads.set_all_muted(bool(body["tg_muted"]))
            set_tg_muted(bool(body["tg_muted"]))
        if body.get("tg_relay") in quiet_threads.RELAY_MODES:
            quiet_threads.set_relay(body["tg_relay"])
            set_tg_relay(body["tg_relay"])
        if "new_chat_telegram" in body:
            set_new_chat_telegram(bool(body["new_chat_telegram"]))
        if body.get("notify_mode") in extras.NOTIFY_MODES:
            extras.set_notify_mode(body["notify_mode"])
            save_notify_mode(body["notify_mode"])
    return web.json_response(
        {
            "tg_muted": quiet_threads.all_muted(),
            "tg_relay": quiet_threads.relay(),
            "new_chat_telegram": new_chat_telegram(),
            "notify_mode": extras.notify_mode(),
        }
    )


async def _probe(request: web.Request) -> web.Response:
    return web.json_response(
        await extras.probe_topic(request.app[_BOT_KEY], _thread_id(request))
    )


async def _sound(request: web.Request) -> web.Response:
    """Telegram sound for the topic (``mode``); browser notifications are the hub's."""
    body = await _json_body(request)
    tid = _thread_id(request)
    result: dict[str, Any] = {"ok": True}
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


# ── app lifecycle ────────────────────────────────────────────────────────────


def build_app(*, bot: Bot | Any) -> web.Application:
    app = web.Application(middlewares=[_middleware], client_max_size=_MAX_BODY)
    app[_BOT_KEY] = bot
    r = app.router
    r.add_post("/api/sessions", _create)
    r.add_post("/api/sessions/{tid}/send", _send)
    r.add_post("/api/sessions/{tid}/rename", _rename)
    r.add_post("/api/sessions/{tid}/sound", _sound)
    r.add_post("/api/sessions/{tid}/probe", _probe)
    r.add_post("/api/sessions/{tid}/detach", _detach)
    r.add_post("/api/sessions/{tid}/keep", _keep)
    r.add_post("/api/sessions/{tid}/delete", _delete)
    r.add_get("/api/settings", _settings)
    r.add_post("/api/settings", _settings)
    return app


_runner: web.AppRunner | None = None
_autotitle_task: asyncio.Task[None] | None = None


def restore_telegram_prefs() -> None:
    """Telegram sound / mute / relay / notify-mode choices, as saved."""
    # Lazy: leaf modules.
    from .. import quiet_threads
    from .prefs import notify_mode, sound_modes, tg_muted, tg_relay

    quiet_threads.load_modes(sound_modes())
    quiet_threads.set_all_muted(tg_muted())
    quiet_threads.set_relay(tg_relay())
    extras.set_notify_mode(notify_mode())


async def start_webui(bot: Bot | Any, bot_data: Any = None) -> None:
    """Start the hub API when ``CCGRAM_WEBUI_PORT`` is set. Never raises.

    ``bot_data`` is the PTB application's, which carries the shared session
    service: the hub's sends, wakes and launches go through the same session
    core as Telegram.
    """
    global _runner, _autotitle_task
    # Lazy: config singleton reads env at import; keep module import cheap.
    from ..config import config

    service.bind_bot_data(bot_data)

    if _runner is not None or not config.webui_port:
        return
    try:
        restore_telegram_prefs()
        runner = web.AppRunner(build_app(bot=bot), shutdown_timeout=2.0)
        await runner.setup()
        await web.TCPSite(
            runner, host=config.webui_host, port=config.webui_port
        ).start()
        _runner = runner
        _hub_token()  # there before the hub's first call
        logger.info("hub API listening on %s:%d", config.webui_host, config.webui_port)
        # Lazy: only the running daemon names sessions.
        from . import autotitle

        _autotitle_task = autotitle.start(bot)
    except OSError as exc:
        logger.error("hub API failed to bind: %s", exc)


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
