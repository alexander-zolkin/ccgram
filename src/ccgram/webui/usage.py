"""Claude plan limits — what ``/usage`` shows in Claude Code — for the web UI.

Asks the endpoint Claude Code itself asks, with Claude Code's own OAuth token,
read-only: the token is never refreshed here (a refresh rotates it and would
log Claude Code out), so once it has expired the answer comes from the copy
Claude Code keeps in ``~/.claude.json`` (``cachedUsageUtilization``, updated
whenever it fetches), marked with its age. Cached for a minute; the endpoint
is undocumented, so every field is read defensively.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

URL = "https://api.anthropic.com/api/oauth/usage"
BETA = "oauth-2025-04-20"
TIMEOUT_S = 10.0
FRESH_S = 60.0

_cache: tuple[float, dict[str, Any] | None] = (0.0, None)
_lock = asyncio.Lock()


def _claude_dir() -> Path:
    return Path(os.getenv("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"))


def _token() -> str | None:
    try:
        data = json.loads((_claude_dir() / ".credentials.json").read_text())
    except OSError, ValueError:
        return None
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if not isinstance(oauth, dict) or not oauth.get("accessToken"):
        return None
    expires = oauth.get("expiresAt")
    if isinstance(expires, (int, float)) and expires / 1000 < time.time() + 30:
        return None  # expired: Claude Code refreshes it on its next request
    return str(oauth["accessToken"])


def _cached_by_claude() -> tuple[float, dict[str, Any]] | None:
    path = Path(os.path.expanduser("~/.claude.json"))
    try:
        cached = json.loads(path.read_text()).get("cachedUsageUtilization")
    except OSError, ValueError, AttributeError:
        return None
    if not isinstance(cached, dict) or not isinstance(cached.get("utilization"), dict):
        return None
    return float(cached.get("fetchedAtMs") or 0) / 1000, cached["utilization"]


def _ts(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _label(item: dict[str, Any]) -> str:
    kind = item.get("kind")
    scope = item.get("scope") or {}
    model = (scope.get("model") or {}).get("display_name") if scope else None
    if kind == "session":
        return "Сессия · 5 часов"
    if kind == "weekly_all":
        return "Неделя · все модели"
    if model:
        return f"Неделя · {model}"
    return str(kind or "лимит")


def _limits(raw: dict[str, Any]) -> list[dict[str, Any]]:
    items = raw.get("limits")
    if isinstance(items, list) and items:
        return [
            {
                "label": _label(it),
                "percent": float(it.get("percent") or 0),
                "resets_at": _ts(it.get("resets_at")),
                "severity": str(it.get("severity") or "normal"),
            }
            for it in items
            if isinstance(it, dict)
        ]
    # Older shape: five_hour / seven_day[_opus|_sonnet] windows.
    named = (
        ("five_hour", "Сессия · 5 часов"),
        ("seven_day", "Неделя · все модели"),
        ("seven_day_opus", "Неделя · Opus"),
        ("seven_day_sonnet", "Неделя · Sonnet"),
    )
    return [
        {
            "label": label,
            "percent": float(win.get("utilization") or 0),
            "resets_at": _ts(win.get("resets_at")),
            "severity": "normal",
        }
        for key, label in named
        if isinstance(win := raw.get(key), dict)
    ]


def _plan() -> str:
    """The subscription, e.g. "Max 5×" (non-secret fields next to the token)."""
    try:
        data = json.loads((_claude_dir() / ".credentials.json").read_text())
        oauth = data.get("claudeAiOauth") or {}
    except OSError, ValueError, AttributeError:
        return ""
    tier = str(oauth.get("rateLimitTier") or "")
    sub = str(oauth.get("subscriptionType") or "")
    for mult in ("20x", "5x"):
        if mult in tier:
            return f"{sub.capitalize() or 'Max'} {mult[:-1]}×"
    return sub.capitalize()


def _money(value: Any) -> float | None:
    """``{amount_minor, exponent}`` → units; plain numbers pass through."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict) and isinstance(value.get("amount_minor"), (int, float)):
        return value["amount_minor"] / 10 ** int(value.get("exponent") or 0)
    return None


def _disabled_reason() -> str:
    """Why extra usage is off, as Claude Code last heard it."""
    try:
        data = json.loads(Path(os.path.expanduser("~/.claude.json")).read_text())
    except OSError, ValueError:
        return ""
    reason = (
        data.get("cachedExtraUsageDisabledReason") if isinstance(data, dict) else ""
    )
    return str(reason or "")


def _credits(raw: dict[str, Any]) -> dict[str, Any]:
    """Usage credits ("extra usage"): what kicks in past the plan limits."""
    extra = raw.get("extra_usage") if isinstance(raw.get("extra_usage"), dict) else {}
    spend = raw.get("spend") if isinstance(raw.get("spend"), dict) else {}
    used = spend.get("used") if isinstance(spend.get("used"), dict) else {}
    enabled = bool(extra.get("is_enabled") or spend.get("enabled"))
    reason = extra.get("disabled_reason") or spend.get("disabled_reason")
    if not enabled and not reason:
        reason = "user_disabled" if extra.get("user_disabled") else _disabled_reason()
    return {
        "enabled": enabled,
        "reason": str(reason or ""),
        "used": _money(spend.get("used"))
        if spend
        else _money(extra.get("used_credits")),
        "limit": _money(spend.get("limit")) or _money(extra.get("monthly_limit")),
        "balance": _money(spend.get("balance")),
        "currency": str(used.get("currency") or extra.get("currency") or "USD"),
        "percent": spend.get("percent"),
        "limit_reached": bool(extra.get("spend_limit_reached")),
        "can_buy": bool(spend.get("can_purchase_credits")),
    }


def _shape(raw: dict[str, Any], as_of: float, live: bool) -> dict[str, Any]:
    breakdown = raw.get("seven_day_breakdown") or {}
    rows = breakdown.get("rows") if isinstance(breakdown, dict) else None
    return {
        "live": live,
        "as_of": as_of,
        "plan": _plan(),
        "limits": _limits(raw),
        "surfaces": [
            {
                "name": str(r.get("display_name") or r.get("key")),
                "percent": r["percent"],
            }
            for r in rows or []
            if isinstance(r, dict) and isinstance(r.get("percent"), (int, float))
        ],
        "credits": _credits(raw),
    }


async def _fetch(token: str) -> dict[str, Any] | None:
    # Lazy: httpx stays off the web UI's import path until the first ask.
    import httpx

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            resp = await client.get(
                URL,
                headers={"Authorization": f"Bearer {token}", "anthropic-beta": BETA},
            )
    except httpx.HTTPError as exc:
        logger.info("plan usage: request failed: %s", exc)
        return None
    if resp.status_code != httpx.codes.OK:
        logger.info("plan usage: HTTP %s", resp.status_code)
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def plan_usage(*, refresh: bool = False) -> dict[str, Any] | None:
    """Limits with their reset times; None when there is nothing to show."""
    global _cache
    async with _lock:
        stamp, data = _cache
        if data is not None and not refresh and time.time() - stamp < FRESH_S:
            return data
        token = await asyncio.to_thread(_token)
        raw = await _fetch(token) if token else None
        if raw is not None:
            data = _shape(raw, time.time(), live=True)
        elif (cached := await asyncio.to_thread(_cached_by_claude)) is not None:
            data = _shape(cached[1], cached[0], live=False)
        else:
            data = None
        _cache = (time.time(), data)
        return data


def last() -> dict[str, Any] | None:
    """What the last call found, without asking (the home page's quick path)."""
    return _cache[1]


def reset_for_testing() -> None:
    global _cache
    _cache = (0.0, None)
