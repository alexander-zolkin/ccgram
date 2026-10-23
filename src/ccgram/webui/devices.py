"""Other machines' Claude Code sessions in the web UI (one hub, many devices).

Each device runs ``kara-node`` (an HTTP agent on its own localhost) and keeps
a reverse SSH tunnel to this host, so the agent answers here on
``127.0.0.1:<port>``. Configured in ``<CCGRAM_DIR>/devices.json``::

    {"devices": [{"id": "pc", "name": "ПК", "port": 18821, "token": "…",
                  "backup": "/tank/backups/conversations/spokes/wsl/transcripts/claude-projects"}]}

Remote sessions get string ids ``<device>:<session uuid>`` alongside the
numeric Telegram-topic ids, and the page talks to them through the same
``/api/sessions/{tid}/…`` routes (server.py dispatches on the id).

A background loop polls every device's ``/sessions``; the last good list of
each device is kept (also on disk), so an offline device still shows its
sessions, read-only, from the transcript backup already on this host when
there is one. Nothing is copied from a device for this.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from ..config import config
from . import transcript

logger = logging.getLogger(__name__)

POLL_S = 8.0
OFFLINE_AFTER_S = 25.0
HTTP_ERROR = 400
_TID = re.compile(r"^([a-z0-9_-]{1,24}):([0-9a-f-]{36})$")
_CONFIG = "devices.json"
_CACHE = "devices_cache.json"

_state: dict[str, dict[str, Any]] = {}  # device id → {sessions, seen, error}
_cfg: dict[str, Any] = {"mtime": None, "devices": []}
_http: Any = None


class DeviceError(Exception):
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def parse_tid(tid: str) -> tuple[str, str] | None:
    m = _TID.match(tid)
    return (m.group(1), m.group(2)) if m else None


def devices() -> list[dict[str, Any]]:
    path = config.config_dir / _CONFIG
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    if _cfg["mtime"] != mtime:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            listed = data.get("devices") if isinstance(data, dict) else None
        except OSError, ValueError:
            listed = None
        _cfg.update(mtime=mtime, devices=listed if isinstance(listed, list) else [])
    return [d for d in _cfg["devices"] if isinstance(d, dict) and d.get("id")]


def _device(device_id: str) -> dict[str, Any]:
    for d in devices():
        if d["id"] == device_id:
            return d
    raise DeviceError("неизвестное устройство", 404)


def online(device_id: str) -> bool:
    st = _state.get(device_id) or {}
    return time.time() - st.get("seen", 0) < OFFLINE_AFTER_S


# ── HTTP to a node ───────────────────────────────────────────────────────────


async def _session() -> Any:
    global _http
    if _http is None or _http.closed:
        # Lazy: aiohttp's client is only needed when devices are configured.
        import aiohttp

        _http = aiohttp.ClientSession()
    return _http


async def call(
    device_id: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 15.0,
    raw: bool = False,
) -> Any:
    # Lazy: see _session.
    import aiohttp

    d = _device(device_id)
    url = f"http://127.0.0.1:{int(d['port'])}{path}"
    http = await _session()
    try:
        async with http.request(
            method,
            url,
            params={k: str(v) for k, v in (params or {}).items() if v is not None},
            json=body,
            headers={"X-Kara-Node-Token": str(d.get("token", ""))},
            timeout=aiohttp.ClientTimeout(total=timeout, connect=3),
        ) as resp:
            data = await resp.read()
            if resp.status >= HTTP_ERROR:
                try:
                    err = json.loads(data).get("error")
                except ValueError, AttributeError:
                    err = None
                raise DeviceError(
                    err or f"{d.get('name', device_id)}: HTTP {resp.status}",
                    resp.status,
                )
            if raw:
                return resp.headers.get("Content-Type", ""), data
            return json.loads(data or b"{}")
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        raise DeviceError(f"{d.get('name', device_id)} не в сети", 503) from exc


# ── the session list ─────────────────────────────────────────────────────────


def _load_cache() -> None:
    if _state:
        return
    try:
        data = json.loads((config.config_dir / _CACHE).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return
    if isinstance(data, dict):
        for dev, sessions in data.items():
            if isinstance(sessions, list):
                _state[dev] = {"sessions": sessions, "seen": 0}


def _save_cache() -> None:
    data = {dev: st.get("sessions", []) for dev, st in _state.items()}
    path = config.config_dir / _CACHE
    tmp = path.with_suffix(".tmp")
    with contextlib.suppress(OSError):
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)


async def refresh(device_id: str) -> None:
    try:
        data = await call(
            device_id, "GET", "/sessions", params={"days": 30}, timeout=10
        )
    except DeviceError as exc:
        _state.setdefault(device_id, {"sessions": [], "seen": 0})["error"] = str(exc)
        return
    st = _state.setdefault(device_id, {})
    changed = st.get("sessions") != data.get("sessions")
    st.update(sessions=data.get("sessions") or [], seen=time.time(), error=None)
    if changed:
        _save_cache()


async def _loop() -> None:
    _load_cache()
    while True:
        try:
            await asyncio.gather(*(refresh(d["id"]) for d in devices()))
        except Exception:  # noqa: BLE001 — the loop must outlive any one device
            logger.exception("devices: refresh failed")
        await asyncio.sleep(POLL_S)


def start() -> asyncio.Task[None]:
    """The poll loop; it idles while devices.json lists nothing (re-read each pass)."""
    return asyncio.get_running_loop().create_task(_loop())


def _web_session(dev: dict[str, Any], s: dict[str, Any], up: bool) -> dict[str, Any]:
    live = s.get("live") if up else None
    status = (live or {}).get("status")
    state = (
        "offline"
        if not up
        else ("active" if status == "busy" else "idle" if live else "dead")
    )
    return {
        "thread_id": f"{dev['id']}:{s['id']}",
        "name": s.get("title") or Path(s.get("cwd") or "").name or s["id"][:8],
        "state": state,
        "last_activity": s.get("last_activity") or 0,
        "provider": "claude",
        "web_only": True,
        "remote": True,
        "device": dev["id"],
        "device_name": dev.get("name") or dev["id"],
        "online": up,
        "writable": bool(live and live.get("writable")),
        "cwd": s.get("cwd") or "",
        "window_id": None,
        "pinned": False,
    }


def sessions() -> list[dict[str, Any]]:
    """Every device's sessions as the page lists them (no I/O: the loop's cache)."""
    _load_cache()
    out = []
    for dev in devices():
        st = _state.get(dev["id"]) or {}
        up = online(dev["id"])
        out.extend(
            _web_session(dev, s, up) for s in st.get("sessions") or [] if s.get("id")
        )
    return out


def status() -> list[dict[str, Any]]:
    return [
        {
            "id": d["id"],
            "name": d.get("name") or d["id"],
            "online": online(d["id"]),
            "seen": (_state.get(d["id"]) or {}).get("seen") or None,
            "error": (_state.get(d["id"]) or {}).get("error"),
        }
        for d in devices()
    ]


# ── one session ──────────────────────────────────────────────────────────────


def _backup_path(device_id: str, sid: str) -> Path | None:
    root = _device(device_id).get("backup")
    if not root:
        return None
    for path in Path(root).glob(f"*/{sid}.jsonl"):
        return path
    return None


async def entries(
    tid: str, *, limit: int, before: int | None, after: int | None
) -> dict[str, Any]:
    dev, sid = parse_tid(tid) or ("", "")
    if online(dev):
        data = await call(
            dev, "GET", f"/sessions/{sid}/entries",
            params={"limit": limit, "before": before, "after": after},
        )  # fmt: skip
        return {**data, "structured": True, "source": "live"}
    path = await asyncio.to_thread(_backup_path, dev, sid)
    if path is None:
        raise DeviceError(
            f"{_device(dev).get('name', dev)} не в сети, копии этого чата здесь нет",
            503,
        )
    if after is not None:
        result = await asyncio.to_thread(transcript.read_after, path, after)
    else:
        result = await asyncio.to_thread(transcript.read_tail, path, limit, before)
    return {**result, "structured": True, "source": "backup"}


async def pane(tid: str) -> dict[str, Any]:
    # Lazy: terminal parser only when a pane is inspected.
    from ..terminal_parser import extract_interactive_content

    dev, sid = parse_tid(tid) or ("", "")
    if not online(dev):
        return {"alive": False, "text": "", "interactive": None, "meta": None}
    data = await call(dev, "GET", f"/sessions/{sid}/pane", timeout=8)
    text = data.get("text") or ""
    inter = extract_interactive_content(text) if text else None
    return {
        "alive": bool(data.get("alive")),
        "text": text,
        "meta": None,
        "interactive": {"name": inter.name, "content": inter.content}
        if inter
        else None,
    }


async def send(tid: str, text: str) -> dict[str, Any]:
    dev, sid = parse_tid(tid) or ("", "")
    text = text.strip()
    if not text:
        raise DeviceError("пустое сообщение", 400)
    await call(dev, "POST", f"/sessions/{sid}/send", body={"text": text})
    asyncio.get_running_loop().create_task(refresh(dev))
    return {"ok": True, "woke": False, "window_id": None}


async def key(tid: str, name: str) -> dict[str, Any]:
    dev, sid = parse_tid(tid) or ("", "")
    await call(dev, "POST", f"/sessions/{sid}/key", body={"key": name})
    return {"ok": True}


async def image(tid: str, ref: str, agent: str = "") -> tuple[str, bytes]:
    dev, sid = parse_tid(tid) or ("", "")
    if not online(dev):
        path = await asyncio.to_thread(_backup_path, dev, sid)
        found = transcript.image_at(path, ref) if path and not agent else None
        if found is None:
            raise DeviceError("картинка недоступна", 404)
        return found
    path = f"/sessions/{sid}/image"
    ctype, data = await call(
        dev, "GET", path, params={"ref": ref, "agent": agent or None}, raw=True
    )
    return ctype.split(";")[0], data


async def agents(tid: str) -> dict[str, Any]:
    dev, sid = parse_tid(tid) or ("", "")
    if not online(dev):
        return {"agents": [], "now": time.time()}
    data = await call(dev, "GET", f"/sessions/{sid}/agents")
    return {"agents": data.get("agents") or [], "now": time.time()}


async def agent_entries(tid: str, aid: str, **kw: Any) -> dict[str, Any]:
    dev, sid = parse_tid(tid) or ("", "")
    return await call(dev, "GET", f"/sessions/{sid}/agents/{aid}/entries", params=kw)


async def create(
    device_id: str, cwd: str, text: str, args: list[str]
) -> dict[str, Any]:
    data = await call(
        device_id, "POST", "/sessions/new",
        body={"cwd": cwd, "text": text, "args": args}, timeout=40,
    )  # fmt: skip
    asyncio.get_running_loop().create_task(refresh(device_id))
    return {"thread_id": f"{device_id}:{data['id']}", "window_id": None, "name": ""}
