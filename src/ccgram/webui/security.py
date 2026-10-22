"""Web UI password (changeable from the UI), session secret and a login log.

``<CCGRAM_DIR>/webui_auth.json`` (0600) overrides ``CCGRAM_WEBUI_PASSWORD`` once
the password is changed in the UI: it keeps a scrypt hash, never the text.
Session cookies are signed with a secret derived from the bot token and either
the env password (nothing changed yet — old cookies keep working) or a random
``cookie_secret`` that is rotated on a password change or "sign out everywhere
else", which logs every other browser out.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any

MIN_PASSWORD = 8
LOG_KEEP = 30
SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def _path() -> Path:
    base = os.getenv("CCGRAM_DIR") or os.path.expanduser("~/.ccgram")
    return Path(base) / "webui_auth.json"


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".webui_auth.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, **SCRYPT).hex()


def check_password(candidate: str, env_password: str) -> bool:
    stored = _load().get("password")
    if isinstance(stored, dict):
        expected = _hash(candidate, bytes.fromhex(stored["salt"]))
        return hmac.compare_digest(expected, stored["hash"])
    return bool(env_password) and hmac.compare_digest(
        candidate.encode(), env_password.encode()
    )


def cookie_material(env_password: str) -> str:
    """What the session cookie is signed with (see the module docstring)."""
    secret = _load().get("cookie_secret")
    return f"secret:{secret}" if secret else env_password


def set_password(new: str) -> None:
    if len(new) < MIN_PASSWORD:
        raise ValueError(f"at least {MIN_PASSWORD} characters")
    data = _load()
    salt = secrets.token_bytes(16)
    data["password"] = {"salt": salt.hex(), "hash": _hash(new, salt)}
    data["password_changed"] = time.time()
    data["cookie_secret"] = secrets.token_hex(32)
    _save(data)


def sign_out_everywhere() -> None:
    data = _load()
    data["cookie_secret"] = secrets.token_hex(32)
    data["signed_out_all"] = time.time()
    _save(data)


def record(method: str, ok: bool, device: str, ip: str) -> None:
    data = _load()
    log = data.get("logins") if isinstance(data.get("logins"), list) else []
    log.append(
        {"ts": time.time(), "method": method, "ok": ok, "device": device, "ip": ip}
    )
    data["logins"] = log[-LOG_KEEP:]
    _save(data)


def overview() -> dict[str, Any]:
    data = _load()
    log = data.get("logins") if isinstance(data.get("logins"), list) else []
    day_ago = time.time() - 86400
    return {
        "password_set_here": isinstance(data.get("password"), dict),
        "password_changed": data.get("password_changed"),
        "logins": [e for e in reversed(log) if e.get("ok")][:10],
        "failed_24h": sum(
            1 for e in log if not e.get("ok") and e.get("ts", 0) > day_ago
        ),
    }
