"""Home page of the web UI: the assistant's own state at a glance.

Optional and driven by ``<CCGRAM_DIR>/hub.toml``; with no such file the API
says ``enabled: false`` and the home screen stays a plain greeting. Personal
paths and hosts live only in that file, never in the repo::

    [memory]
    dir = "~/.claude/projects/<project>/memory"  # markdown memory store
    pending = "pending.md"                        # "- [ ] YYYY-MM-DD — text"
    done = "_archive/pending-done.md"             # closed items appended here
    map = "/var/www/memory-map"                   # static site shown in an iframe

    [[reach]]                                     # where the agent can get to
    name = "PC"
    ssh = "user@192.168.0.10"                     # or tcp = "host:22", or local = true
    key = "~/.ssh/pc.key"
    access = "SSH: shell, files"

    [inventory]                                   # a fleet file, TCP-probed
    name = "VPN nodes"
    file = "~/fleet.toml"                         # [[servers]] host/label/scopes
    scope = "monitor"
    port = 22

Sections: ``now`` (working chats, today's tokens), ``reach`` (probed every
five minutes in the background), ``pending`` (close / snooze write the
markdown back) and ``memory`` (search, today's changes, one file).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import subprocess
import time
import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .service import WebUIError

logger = logging.getLogger(__name__)

PROBE_EVERY = 300.0
SSH_TIMEOUT = 12.0
TCP_TIMEOUT = 5.0
SEARCH_LIMIT = 20
MAX_SNOOZE_DAYS = 365
RECENT_CHATS = 6
_SKIP_MEMORY = {"index.md", "MEMORY.md"}


def _base() -> Path:
    return Path(os.getenv("CCGRAM_DIR") or os.path.expanduser("~/.ccgram"))


_conf_cache: tuple[float, dict[str, Any]] = (-1.0, {})


def config() -> dict[str, Any]:
    """``hub.toml``, re-read when it changes; ``{}`` when absent or broken."""
    global _conf_cache
    path = _base() / "hub.toml"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if mtime != _conf_cache[0]:
        try:
            _conf_cache = (mtime, tomllib.loads(path.read_text(encoding="utf-8")))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            logger.warning("hub.toml unreadable: %s", exc)
            _conf_cache = (mtime, {})
    return _conf_cache[1]


def _expand(value: str) -> Path:
    return Path(os.path.expanduser(value))


# ── now: what is running, what today cost ────────────────────────────────────


def _midnight() -> float:
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


async def now() -> dict[str, Any]:
    # Lazy: service pulls the handler graph.
    from . import service

    sessions = await service.list_sessions()
    since = _midnight()
    paths: list[Path] = []
    for s in sessions:
        if s.get("last_activity", 0) < since:
            continue
        with contextlib.suppress(WebUIError):
            tx, _provider = service._transcript_for(
                service.find_binding(s["thread_id"])
            )
            if tx:
                paths.append(tx)
    totals = {"out": 0, "turns": 0, "ms": 0}
    for stats in await asyncio.gather(
        *(asyncio.to_thread(_day_stats, p, since) for p in paths)
    ):
        for key in totals:
            totals[key] += stats[key]
    return {
        "working": [
            {"thread_id": s["thread_id"], "name": s["name"]}
            for s in sessions
            if s["state"] == "active"
        ],
        "recent": [
            {"thread_id": s["thread_id"], "name": s["name"], "at": s["last_activity"]}
            for s in sorted(sessions, key=lambda s: -s.get("last_activity", 0))
            if s["state"] != "active" and s.get("last_activity", 0) >= since
        ][:RECENT_CHATS],
        "alive": sum(1 for s in sessions if s["state"] != "dead"),
        "today": totals,
        "chats_today": len(paths),
    }


_day_cache: dict[str, tuple[int, float, dict[str, int]]] = {}


def _day_stats(path: Path, since: float) -> dict[str, int]:
    # Lazy: transcript is a leaf, keep it next to its user.
    from . import transcript

    size = transcript.file_size(path)
    hit = _day_cache.get(str(path))
    if hit and hit[0] == size and hit[1] == since:
        return hit[2]
    stats = transcript.day_stats(path, since)
    _day_cache[str(path)] = (size, since, stats)
    return stats


# ── reach: where the agent can get to ───────────────────────────────────────

_reach: dict[str, dict[str, Any]] = {}
_probe_lock = asyncio.Lock()


def _state_path() -> Path:
    return _base() / "hub_reach.json"


def _load_seen() -> dict[str, float]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return {k: float(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _save_seen(seen: dict[str, float]) -> None:
    path = _state_path()
    tmp = path.with_suffix(".tmp")
    with contextlib.suppress(OSError):
        tmp.write_text(json.dumps(seen), encoding="utf-8")
        os.replace(tmp, path)


def _local_detail() -> dict[str, Any]:
    detail: dict[str, Any] = {"load": round(os.getloadavg()[0], 2)}
    with contextlib.suppress(OSError, ValueError, IndexError):
        detail["uptime_s"] = int(float(Path("/proc/uptime").read_text().split()[0]))
    with contextlib.suppress(OSError, ValueError):
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                detail["mem_avail_mb"] = int(line.split()[1]) // 1024
    return detail


_SSH_REASONS = (
    ("No route to host", "не в сети"),
    ("Connection timed out", "нет ответа"),
    ("Connection refused", "SSH закрыт"),
    ("Permission denied", "ключ не принят"),
    ("Could not resolve", "имя не находится"),
    ("Host key verification failed", "сменился ключ хоста"),
)


async def _probe_ssh(target: str, key: str | None) -> tuple[bool, str]:
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
    cmd += ["-o", "StrictHostKeyChecking=accept-new"]
    if key:
        cmd += ["-i", str(_expand(key))]
    # "exit" works in sh and in Windows cmd.exe alike.
    cmd += [target, "exit"]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), SSH_TIMEOUT)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        return False, "нет ответа"
    if proc.returncode == 0:
        return True, ""
    text = err.decode("utf-8", "replace")
    for needle, reason in _SSH_REASONS:
        if needle in text:
            return False, reason
    return False, "не отвечает"


async def _probe_tcp(host: str, port: int) -> tuple[bool, str]:
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), TCP_TIMEOUT
        )
    except OSError, TimeoutError:
        return False, "не отвечает"
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return True, ""


def _split_hostport(value: str, default: int = 22) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    if host and port.isdigit():
        return host, int(port)
    return value, default


async def _probe(target: dict[str, Any]) -> tuple[bool, str]:
    if target.get("local"):
        return True, ""
    if target.get("ssh"):
        return await _probe_ssh(str(target["ssh"]), target.get("key"))
    if target.get("tcp"):
        return await _probe_tcp(*_split_hostport(str(target["tcp"])))
    return False, "не настроено"


def _inventory(conf: dict[str, Any]) -> list[dict[str, Any]]:
    inv = conf.get("inventory") or {}
    if not inv.get("file"):
        return []
    try:
        fleet = tomllib.loads(_expand(inv["file"]).read_text(encoding="utf-8"))
    except OSError, tomllib.TOMLDecodeError:
        return []
    scope, port, seen = inv.get("scope"), int(inv.get("port", 22)), set()
    nodes = []
    for server in fleet.get("servers", []):
        host = server.get("host")
        if not host or host in seen:
            continue
        if scope and scope not in server.get("scopes", []):
            continue
        seen.add(host)
        nodes.append(
            {
                "name": server.get("label") or server.get("id") or host,
                "tcp": f"{host}:{port}",
            }
        )
    return nodes


async def probe_all() -> None:
    """Probe every target once; remember when each was last reachable."""
    async with _probe_lock:
        conf = config()
        targets = [dict(t, group=None) for t in conf.get("reach", [])]
        group = (conf.get("inventory") or {}).get("name") or "inventory"
        targets += [dict(t, group=group) for t in _inventory(conf)]
        results = await asyncio.gather(*(_probe(t) for t in targets))
        seen = _load_seen()
        stamp = time.time()
        fresh: dict[str, dict[str, Any]] = {}
        for target, (ok, why) in zip(targets, results, strict=True):
            key = f"{target['group'] or ''}/{target['name']}"
            if ok:
                seen[key] = stamp
            fresh[key] = {
                "name": target["name"],
                "group": target["group"],
                "access": target.get("access", ""),
                "local": bool(target.get("local")),
                "ok": ok,
                "why": why,
                "checked": stamp,
                "last_ok": seen.get(key),
            }
        _save_seen(seen)
        _reach.clear()
        _reach.update(fresh)


async def reach(refresh: bool = False) -> dict[str, Any]:
    if refresh or not _reach:
        await probe_all()
    rows = [r for r in _reach.values() if not r["group"]]
    for row in rows:
        if row["local"]:
            row["detail"] = _local_detail()
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in _reach.values():
        if r["group"]:
            groups.setdefault(r["group"], []).append(r)
    return {
        "targets": rows,
        "groups": [
            {"name": name, "ok": sum(1 for r in items if r["ok"]), "items": items}
            for name, items in groups.items()
        ],
    }


async def run() -> None:
    while True:
        try:
            if config().get("reach") or config().get("inventory"):
                await probe_all()
        except Exception:  # noqa: BLE001 — a probe must never kill the loop
            logger.exception("hub probe failed")
        await asyncio.sleep(PROBE_EVERY)


def start() -> asyncio.Task[None]:
    return asyncio.get_running_loop().create_task(run(), name="webui-hub-reach")


# ── pending: the assistant's promised follow-ups ─────────────────────────────

_ITEM = re.compile(r"^- \[ \] (\d{4}-\d{2}-\d{2}) — (.+)$")


def _memory_dir() -> Path:
    conf = config().get("memory") or {}
    if not conf.get("dir"):
        raise WebUIError("memory is not configured", 404)
    return _expand(conf["dir"]).resolve()


def _pending_path() -> Path:
    name = (config().get("memory") or {}).get("pending", "pending.md")
    return _memory_dir() / name


def _item_id(line: str) -> str:
    return hashlib.sha1(line.encode()).hexdigest()[:12]


def pending() -> dict[str, Any]:
    try:
        lines = _pending_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"items": []}
    today = date.today()
    items = []
    for line in lines:
        m = _ITEM.match(line.strip())
        if not m:
            continue
        due = date.fromisoformat(m.group(1))
        items.append(
            {
                "id": _item_id(line.strip()),
                "date": m.group(1),
                "days": (due - today).days,
                "text": m.group(2),
            }
        )
    items.sort(key=lambda it: it["date"])
    return {"items": items}


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _take_line(item_id: str) -> tuple[Path, list[str], int]:
    path = _pending_path()
    lines = path.read_text(encoding="utf-8").split("\n")
    for idx, line in enumerate(lines):
        if _ITEM.match(line.strip()) and _item_id(line.strip()) == item_id:
            return path, lines, idx
    raise WebUIError("this item changed or is gone — reload", 409)


def close_pending(item_id: str) -> dict[str, Any]:
    """Move the item to the done log (pending holds open items only)."""
    path, lines, idx = _take_line(item_id)
    item = lines.pop(idx).strip().removeprefix("- [ ] ")
    done_name = (config().get("memory") or {}).get("done", "_archive/pending-done.md")
    done = _memory_dir() / done_name
    with done.open("a", encoding="utf-8") as fh:
        fh.write(
            f"- [x] (closed {date.today()}: closed by the user in the web UI) {item}\n"
        )
    _write_atomic(path, "\n".join(lines))
    return pending()


def snooze_pending(item_id: str, days: int) -> dict[str, Any]:
    if not 1 <= days <= MAX_SNOOZE_DAYS:
        raise WebUIError(f"days must be 1..{MAX_SNOOZE_DAYS}")
    path, lines, idx = _take_line(item_id)
    m = _ITEM.match(lines[idx].strip())
    assert m is not None  # _take_line only returns matching lines
    start = max(date.fromisoformat(m.group(1)), date.today())
    new_date = start + timedelta(days=days)
    indent = lines[idx][: len(lines[idx]) - len(lines[idx].lstrip())]
    lines[idx] = f"{indent}- [ ] {new_date} — {m.group(2)}"
    _write_atomic(path, "\n".join(lines))
    return pending()


# ── memory: what the assistant knows ─────────────────────────────────────────

_FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+\.md)\)")
_docs: dict[str, tuple[float, dict[str, Any]]] = {}


def _front_field(front: str, key: str) -> str:
    m = re.search(rf"^\s*{key}:\s*(.+)$", front, re.M)
    return m.group(1).strip().strip("\"'") if m else ""


def _titles(root: Path) -> dict[str, str]:
    """Human titles from the ``[Title](file.md)`` pointers in index files."""
    titles: dict[str, str] = {}
    for index in root.glob("*/index.md"):
        with contextlib.suppress(OSError):
            for title, target in _LINK.findall(index.read_text(encoding="utf-8")):
                rel = (index.parent / target).resolve()
                with contextlib.suppress(ValueError):
                    titles.setdefault(str(rel.relative_to(root)), title)
    return titles


def _is_memory(rel: Path) -> bool:
    return (
        rel.suffix == ".md"
        and rel.name not in _SKIP_MEMORY
        and not any(part.startswith(("_", ".")) for part in rel.parts)
        and len(rel.parts) > 1  # root files (pending.md, router) are not memories
    )


def _doc(root: Path, rel: Path, titles: dict[str, str]) -> dict[str, Any] | None:
    path = root / rel
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _docs.get(str(rel))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None
    m = _FRONT.match(text)
    front, body = (m.group(1), text[m.end() :]) if m else ("", text)
    doc = {
        "path": str(rel),
        "domain": rel.parts[0],
        "title": titles.get(str(rel)) or _front_field(front, "name") or rel.stem,
        "description": _front_field(front, "description"),
        "type": _front_field(front, "type"),
        "mtime": mtime,
        "_meta": "",
        "_body": body.lower(),
    }
    doc["_meta"] = f"{doc['title']} {doc['description']} {rel.stem}".lower()
    _docs[str(rel)] = (mtime, doc)
    return doc


def _public(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def _all_docs(root: Path) -> list[dict[str, Any]]:
    titles = _titles(root)
    docs = []
    for path in root.rglob("*.md"):
        rel = path.relative_to(root)
        if _is_memory(rel) and (doc := _doc(root, rel, titles)):
            docs.append(doc)
    return docs


def _changed_today(root: Path) -> dict[str, str]:
    """``{rel: "new"|"changed"}`` since the store's last commit (nightly)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "-uall", "--", "."],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
    except OSError, subprocess.SubprocessError:
        since = _midnight()
        return {
            str(p.relative_to(root)): "changed"
            for p in root.rglob("*.md")
            if p.stat().st_mtime >= since
        }
    changed = {}
    for line in out.splitlines():
        code, rel = line[:2], line[3:].strip().strip('"')
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1]
        if "D" in code:
            continue
        changed[rel] = "new" if code in ("??", "A ") else "changed"
    return changed


def memory_overview() -> dict[str, Any]:
    root = _memory_dir()
    docs = _all_docs(root)
    by_path = {d["path"]: d for d in docs}
    today = []
    for rel, kind in _changed_today(root).items():
        doc = by_path.get(rel)
        if doc:
            today.append(dict(_public(doc), change=kind))
    today.sort(key=lambda d: -d["mtime"])
    return {
        "total": len(docs),
        "domains": len({d["domain"] for d in docs}),
        "today": today,
    }


def memory_search(query: str) -> dict[str, Any]:
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
    if not words:
        return {"results": []}
    scored = []
    for doc in _all_docs(_memory_dir()):
        score = 0
        for word in words:
            meta, body = doc["_meta"].count(word), doc["_body"].count(word)
            if not meta and not body:
                break
            score += 4 * meta + min(body, 6)
        else:
            scored.append((score, doc["mtime"], doc))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return {"results": [_public(d) for _s, _m, d in scored[:SEARCH_LIMIT]]}


def memory_file(rel: str) -> dict[str, Any]:
    root = _memory_dir()
    path = (root / rel).resolve()
    try:
        inside = path.relative_to(root)
    except ValueError:
        raise WebUIError("not in the memory store", 403) from None
    if not _is_memory(inside) or not path.is_file():
        raise WebUIError("no such memory", 404)
    text = path.read_text(encoding="utf-8")
    m = _FRONT.match(text)
    doc = _doc(root, inside, _titles(root)) or {}
    return dict(_public(doc), body=text[m.end() :] if m else text)


def map_file(tail: str) -> Path:
    """A file of the memory map site (``[memory] map``), served in an iframe."""
    conf = config().get("memory") or {}
    if not conf.get("map"):
        raise WebUIError("no memory map", 404)
    root = _expand(conf["map"]).resolve()
    path = (root / (tail or "index.html")).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise WebUIError("not found", 404) from None
    if not path.is_file():
        raise WebUIError("not found", 404)
    return path


# ── everything for the home page in one call ─────────────────────────────────


async def overview() -> dict[str, Any]:
    conf = config()
    if not conf:
        return {"enabled": False}
    has_memory = bool((conf.get("memory") or {}).get("dir"))
    now_data, reach_data = await asyncio.gather(now(), reach())
    return {
        "enabled": True,
        "now": now_data,
        "reach": reach_data,
        "pending": await asyncio.to_thread(pending) if has_memory else None,
        "memory": await asyncio.to_thread(memory_overview) if has_memory else None,
        "map": bool((conf.get("memory") or {}).get("map")),
    }
