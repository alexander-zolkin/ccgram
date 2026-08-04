"""Tests for private Grok session isolation helpers."""

from __future__ import annotations

import json
import os
import stat

from ccgram.providers import grok_private


def test_private_root_default(monkeypatch) -> None:
    monkeypatch.delenv("CCGRAM_PRIVATE_DIR", raising=False)
    assert grok_private.private_root().name == "private"


def test_private_root_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCGRAM_PRIVATE_DIR", str(tmp_path / "priv"))
    assert grok_private.private_root() == tmp_path / "priv"
    assert grok_private.private_grok_home() == tmp_path / "priv" / ".grok-home"


def test_create_private_cwd_is_isolated(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCGRAM_PRIVATE_DIR", str(tmp_path / "priv"))
    cwd = grok_private.create_private_cwd()
    assert cwd.is_dir()
    assert cwd.name.startswith("grok-")
    assert grok_private.is_private_path(cwd) is True
    # Two calls never collide.
    assert grok_private.create_private_cwd() != cwd


def test_is_private_path_excludes_outside(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCGRAM_PRIVATE_DIR", str(tmp_path / "priv"))
    (tmp_path / "priv").mkdir(parents=True)
    assert grok_private.is_private_path(tmp_path / "priv" / "grok-x") is True
    assert grok_private.is_private_path("/home/openclaw/.openclaw/workspace") is False
    assert grok_private.is_private_path("/home/openclaw/.grok/sessions") is False


def test_ensure_private_home_links_auth_and_installs_hooks(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("CCGRAM_PRIVATE_DIR", str(tmp_path / "priv"))
    # Fake main grok home with an auth.json to share.
    main = tmp_path / "maingrok"
    main.mkdir()
    (main / "auth.json").write_text("{}")
    monkeypatch.setenv("GROK_HOME", str(main))

    home = grok_private.ensure_grok_private_home()
    assert home == tmp_path / "priv" / ".grok-home"
    # auth is shared via symlink to the main login.
    assert (home / "auth.json").is_symlink()
    assert os.readlink(home / "auth.json") == str(main / "auth.json")
    # ccgram grok hooks installed into the private home.
    hooks = json.loads((home / "hooks" / "ccgram.json").read_text())
    assert "SessionStart" in hooks["hooks"]
    assert "--provider grok" in hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    # Private root is not world/group readable.
    assert stat.S_IMODE((tmp_path / "priv").stat().st_mode) == 0o700


def test_ensure_private_home_idempotent(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCGRAM_PRIVATE_DIR", str(tmp_path / "priv"))
    main = tmp_path / "maingrok"
    main.mkdir()
    (main / "auth.json").write_text("{}")
    monkeypatch.setenv("GROK_HOME", str(main))
    grok_private.ensure_grok_private_home()
    # Second call must not raise or duplicate.
    home = grok_private.ensure_grok_private_home()
    hooks = json.loads((home / "hooks" / "ccgram.json").read_text())
    assert len(hooks["hooks"]["SessionStart"]) == 1
