"""Passkey (WebAuthn) login for the web UI.

Credentials live in ``<CCGRAM_DIR>/webui_passkeys.json`` (0600). It holds only
public keys, so there is nothing in it worth stealing. Keys are discoverable
and user verification is required (Face ID / Touch ID / Windows Hello / a PIN),
so the login page needs no user name. The relying party is the host the page
is served from; browsers allow WebAuthn only over https (or on localhost).
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

CHALLENGE_TTL_S = 300
MAX_CHALLENGES = 64
MAX_NAME = 60
RP_NAME = "Кара"


class PasskeyError(Exception):
    """A registration or login that must not succeed."""


def _path() -> Path:
    base = os.getenv("CCGRAM_DIR") or os.path.expanduser("~/.ccgram")
    return Path(base) / "webui_passkeys.json"


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except OSError, ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("credentials", [])
    return data


def _save(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".webui_passkeys.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _public(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: entry.get(k) for k in ("id", "name", "created", "last_used", "synced")}


def credentials() -> list[dict[str, Any]]:
    return [_public(c) for c in _load()["credentials"]]


def count() -> int:
    return len(_load()["credentials"])


# ── one-time challenges ──────────────────────────────────────────────────────

_challenges: dict[str, tuple[bytes, float, str]] = {}


def _remember(challenge: bytes, kind: str) -> str:
    now = time.time()
    for token in [t for t, (_c, exp, _k) in _challenges.items() if exp < now]:
        del _challenges[token]
    while len(_challenges) >= MAX_CHALLENGES:
        del _challenges[next(iter(_challenges))]
    token = secrets.token_urlsafe(18)
    _challenges[token] = (challenge, now + CHALLENGE_TTL_S, kind)
    return token


def _take(token: str, kind: str) -> bytes:
    """A challenge is good for exactly one attempt."""
    hit = _challenges.pop(token, None)
    if hit is None or hit[1] < time.time() or hit[2] != kind:
        raise PasskeyError("the request expired — try again")
    return hit[0]


# ── registration (only from an already logged-in browser) ────────────────────


def registration_options(rp_id: str) -> dict[str, Any]:
    data = _load()
    if "user_id" not in data:
        data["user_id"] = bytes_to_base64url(secrets.token_bytes(16))
        _save(data)
    opts = generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_id=base64url_to_bytes(data["user_id"]),
        user_name="kara",
        user_display_name=RP_NAME,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["id"]))
            for c in data["credentials"]
        ],
    )
    return {
        "token": _remember(opts.challenge, "register"),
        "options": json.loads(options_to_json(opts)),
    }


def register(
    rp_id: str, origin: str, token: str, credential: dict[str, Any], name: str
) -> dict[str, Any]:
    challenge = _take(token, "register")
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_origin=origin,
            expected_rp_id=rp_id,
            require_user_verification=True,
        )
    except InvalidRegistrationResponse as exc:
        raise PasskeyError(f"the key was not accepted: {exc}") from None
    entry = {
        "id": bytes_to_base64url(verified.credential_id),
        "public_key": bytes_to_base64url(verified.credential_public_key),
        "sign_count": verified.sign_count,
        "name": (name.strip() or "ключ")[:MAX_NAME],
        "created": time.time(),
        "last_used": None,
        "synced": bool(verified.credential_backed_up),
    }
    data = _load()
    data["credentials"] = [c for c in data["credentials"] if c["id"] != entry["id"]]
    data["credentials"].append(entry)
    _save(data)
    return _public(entry)


def remove(cred_id: str) -> bool:
    data = _load()
    kept = [c for c in data["credentials"] if c["id"] != cred_id]
    if len(kept) == len(data["credentials"]):
        return False
    data["credentials"] = kept
    _save(data)
    return True


# ── login ────────────────────────────────────────────────────────────────────


def login_options(rp_id: str) -> dict[str, Any]:
    opts = generate_authentication_options(
        rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED
    )
    return {
        "token": _remember(opts.challenge, "login"),
        "options": json.loads(options_to_json(opts)),
    }


def login(
    rp_id: str, origin: str, token: str, credential: dict[str, Any]
) -> dict[str, Any]:
    challenge = _take(token, "login")
    data = _load()
    cred_id = str(credential.get("id") or "")
    entry = next((c for c in data["credentials"] if c["id"] == cred_id), None)
    if entry is None:
        raise PasskeyError("this key is not registered here")
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=base64url_to_bytes(entry["public_key"]),
            credential_current_sign_count=int(entry.get("sign_count") or 0),
            require_user_verification=True,
        )
    except InvalidAuthenticationResponse as exc:
        raise PasskeyError(f"the key was not accepted: {exc}") from None
    entry["sign_count"] = verified.new_sign_count
    entry["last_used"] = time.time()
    _save(data)
    return _public(entry)


def reset_for_testing() -> None:
    _challenges.clear()
