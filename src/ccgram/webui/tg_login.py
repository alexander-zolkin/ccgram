"""Let a new device into the web UI with one tap in Telegram.

The login page asks; the bot posts the device, its IP and a two-digit code
(also shown on that page) to the group's General topic with ✅ / ❌; the page
polls and receives its session cookie once approved. Anyone on the internet
can ask, so asking is throttled hard — the owner's phone must not become a
spam target — and a request is good for one approval within five minutes.
"""

from __future__ import annotations

import contextlib
import logging
import re
import secrets
import time
from collections import deque
from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from ..config import config
from ..handlers.callback_registry import register

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

PREFIX = "wlogin:"
REQUEST_TTL_S = 300
MIN_GAP_S = 20.0
MAX_PER_HOUR = 6

_requests: dict[str, dict[str, Any]] = {}
_asked: deque[float] = deque()


class TooSoonError(Exception):
    """Login requests are throttled; ``wait`` seconds until the next one."""

    def __init__(self, wait: int) -> None:
        super().__init__(f"wait {wait}s")
        self.wait = wait


def enabled() -> bool:
    return bool(config.group_id)


def device_label(user_agent: str) -> str:
    ua = user_agent or ""
    system = next(
        (
            name
            for pattern, name in (
                (r"iPhone", "iPhone"),
                (r"iPad", "iPad"),
                (r"Android", "Android"),
                (r"Windows", "Windows"),
                (r"Mac OS X|Macintosh", "Mac"),
                (r"CrOS", "ChromeOS"),
                (r"Linux", "Linux"),
            )
            if re.search(pattern, ua)
        ),
        "неизвестное устройство",
    )
    browser = next(
        (
            name
            for pattern, name in (
                (r"YaBrowser", "Яндекс Браузер"),
                (r"Edg/", "Edge"),
                (r"OPR/", "Opera"),
                (r"Firefox/|FxiOS", "Firefox"),
                (r"Chrome/|CriOS", "Chrome"),
                (r"Safari/", "Safari"),
            )
            if re.search(pattern, ua)
        ),
        "",
    )
    return f"{browser} на {system}" if browser else system


def _prune(now: float) -> None:
    for rid in [r for r, q in _requests.items() if q["expires"] + 60 < now]:
        del _requests[rid]
    while _asked and _asked[0] < now - 3600:
        _asked.popleft()


async def start(bot: Any, ip: str, user_agent: str) -> dict[str, Any]:
    """Post the approval request; returns ``{id, code, expires}`` for the page."""
    now = time.time()
    _prune(now)
    if _asked and now - _asked[-1] < MIN_GAP_S:
        raise TooSoonError(int(MIN_GAP_S - (now - _asked[-1])) + 1)
    if len(_asked) >= MAX_PER_HOUR:
        raise TooSoonError(int(_asked[0] + 3600 - now) + 1)
    _asked.append(now)
    rid = secrets.token_urlsafe(18)
    code = f"{secrets.randbelow(90) + 10}"
    device = device_label(user_agent)
    text = (
        "🔐 Вход в веб-интерфейс Кары\n\n"
        f"Устройство: {device}\n"
        f"IP: {ip}\n"
        f"Код на экране: {code}\n\n"
        "Если это не ты — нажми «Нет»."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Впустить", callback_data=f"{PREFIX}ok:{rid}"),
                InlineKeyboardButton("❌ Нет", callback_data=f"{PREFIX}no:{rid}"),
            ]
        ]
    )
    request = {
        "code": code,
        "device": device,
        "ip": ip,
        "expires": now + REQUEST_TTL_S,
        "state": "pending",
    }
    try:
        await bot.send_message(
            chat_id=config.group_id, text=text, reply_markup=keyboard
        )
    except TelegramError as exc:
        logger.warning("webui telegram login: send failed: %s", exc)
        raise
    _requests[rid] = request
    logger.info("webui telegram login asked: %s from %s", device, ip)
    return {"id": rid, "code": code, "expires": request["expires"]}


def poll(rid: str) -> str:
    """pending / approved (exactly once) / denied / expired."""
    request = _requests.get(rid)
    if request is None:
        return "expired"
    if request["state"] == "pending" and request["expires"] < time.time():
        request["state"] = "expired"
    if request["state"] == "approved":
        request["state"] = "used"
        return "approved"
    return "expired" if request["state"] == "used" else request["state"]


@register(PREFIX)
async def on_callback(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    _verb, _, rid = query.data.removeprefix(PREFIX).partition(":")
    request = _requests.get(rid)
    if (
        request is None
        or request["state"] != "pending"
        or request["expires"] < time.time()
    ):
        await query.answer("Запрос устарел")
        return
    approved = _verb == "ok"
    request["state"] = "approved" if approved else "denied"
    logger.info(
        "webui telegram login %s: %s from %s",
        request["state"],
        request["device"],
        request["ip"],
    )
    verdict = "✅ Впустила" if approved else "🚫 Отклонено"
    await query.answer(verdict)
    with contextlib.suppress(TelegramError):
        await query.edit_message_text(
            f"{verdict}: {request['device']}, IP {request['ip']}, код {request['code']}"
        )


def reset_for_testing() -> None:
    _requests.clear()
    _asked.clear()
