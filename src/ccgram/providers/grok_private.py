"""Private Grok session support — isolated from the main assistant's memory.

A *private* Grok session runs in a dedicated folder tree under the private root
(``$CCGRAM_PRIVATE_DIR`` or ``~/private``) with its own ``GROK_HOME``, so neither
the working files nor the conversation transcript land in any path the primary
assistant scans (the workspace, ``~/.grok/sessions``, or ``~/.claude``). The
ccgram bot still reads the transcript (it runs as the same user) to relay the
session to Telegram, but the data physically lives outside the assistant's
reach — the isolation is by location, not by OS user (a separate user would
break the bot's own read/relay path).

Layout::

    $CCGRAM_PRIVATE_DIR/                     (0700)
        .grok-home/                          # private GROK_HOME (history)
            auth.json -> ~/.grok/auth.json   # shared login, private sessions
            hooks/ccgram.json                # ccgram lifecycle hooks
            sessions/…                       # private transcripts
        grok-<timestamp>-<rand>/             # per-session cwd (files)
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime
from pathlib import Path

import structlog

logger = structlog.get_logger()

_PRIVATE_DIR_MODE = 0o700


def private_root() -> Path:
    """Root for all private session data (``$CCGRAM_PRIVATE_DIR`` or ``~/private``)."""
    override = os.environ.get("CCGRAM_PRIVATE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "private"


def private_grok_home() -> Path:
    """The private ``GROK_HOME`` under the private root (shared by private sessions)."""
    return private_root() / ".grok-home"


def _main_grok_home() -> Path:
    override = os.environ.get("GROK_HOME", "").strip()
    if override and ".grok-home" not in override:
        return Path(override).expanduser()
    return Path.home() / ".grok"


def ensure_grok_private_home(*, now: datetime | None = None) -> Path:  # noqa: ARG001
    """Create + prime the private ``GROK_HOME`` (idempotent). Returns its path.

    Shares the main account's ``auth.json`` via symlink (same grok.com login,
    private sessions) and installs ccgram's Grok lifecycle hooks so private
    sessions are tracked/relayed exactly like normal ones.
    """
    root = private_root()
    root.mkdir(parents=True, exist_ok=True)
    _chmod_quietly(root, _PRIVATE_DIR_MODE)

    home = private_grok_home()
    home.mkdir(parents=True, exist_ok=True)
    _chmod_quietly(home, _PRIVATE_DIR_MODE)

    # Share the main login so the private home need not re-authenticate.
    auth_link = home / "auth.json"
    main_auth = _main_grok_home() / "auth.json"
    if not auth_link.exists() and main_auth.exists():
        try:
            auth_link.symlink_to(main_auth)
        except OSError as exc:
            logger.warning("private grok: could not link auth.json: %s", exc)

    _install_hooks_into(home)
    return home


def _install_hooks_into(home: Path) -> None:
    """Install ccgram Grok hooks into *home*/hooks/ccgram.json (idempotent)."""
    # Lazy: hook.py pulls the hook install machinery; only needed on setup.
    from ccgram.hook import _GROK_HOOK_EVENTS, _install_json_hooks

    hooks_file = home / "hooks" / "ccgram.json"
    try:
        _install_json_hooks(hooks_file, "grok", _GROK_HOOK_EVENTS, 5)
    except Exception as exc:  # noqa: BLE001 — hooks are best-effort
        logger.warning("private grok: hook install failed: %s", exc)


def create_private_cwd(*, now: datetime | None = None) -> Path:
    """Create a fresh per-session private working directory and return it."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    cwd = private_root() / f"grok-{stamp}-{secrets.token_hex(2)}"
    cwd.mkdir(parents=True, exist_ok=True)
    _chmod_quietly(cwd, _PRIVATE_DIR_MODE)
    return cwd


def is_private_path(path: str | os.PathLike[str]) -> bool:
    """True when *path* lives under the private root (used to gate memory/scan)."""
    try:
        resolved = Path(path).resolve()
        root = private_root().resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def _chmod_quietly(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError as exc:
        logger.debug("private grok: chmod %s failed: %s", path, exc)
