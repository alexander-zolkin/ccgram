import json
import time

from ccgram.webui import usage

RAW = {
    "five_hour": {"utilization": 2, "resets_at": "2026-10-23T05:00:00+00:00"},
    "limits": [
        {
            "kind": "session",
            "percent": 2,
            "severity": "normal",
            "resets_at": "2026-10-23T05:00:00+00:00",
            "scope": None,
        },
        {
            "kind": "weekly_all",
            "percent": 25,
            "severity": "normal",
            "resets_at": "2026-10-28T15:00:00+00:00",
            "scope": None,
        },
        {
            "kind": "weekly_scoped",
            "percent": 0,
            "severity": "normal",
            "resets_at": "2026-10-28T15:00:00+00:00",
            "scope": {"model": {"display_name": "Fable"}},
        },
    ],
    "seven_day_breakdown": {"rows": [{"display_name": "Claude Code", "percent": 100}]},
}


def _claude_home(tmp_path, monkeypatch, expires_in, cached=None):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    oauth = {
        "accessToken": "tok",
        "expiresAt": (time.time() + expires_in) * 1000,
        "subscriptionType": "max",
        "rateLimitTier": "default_claude_max_5x",
    }
    (home / ".claude" / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": oauth})
    )
    if cached is not None:
        (home / ".claude.json").write_text(
            json.dumps({"cachedUsageUtilization": cached})
        )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    usage.reset_for_testing()


async def test_live_limits_are_labelled_and_cached(tmp_path, monkeypatch):
    _claude_home(tmp_path, monkeypatch, 3600)
    asked = []

    async def fake_fetch(token):
        asked.append(token)
        return RAW

    monkeypatch.setattr(usage, "_fetch", fake_fetch)
    got = await usage.plan_usage()
    assert got["live"] and got["plan"] == "Max 5×"
    assert [(lim["label"], lim["percent"]) for lim in got["limits"]] == [
        ("Сессия · 5 часов", 2.0),
        ("Неделя · все модели", 25.0),
        ("Неделя · Fable", 0.0),
    ]
    assert got["limits"][0]["resets_at"] > 0
    assert got["surfaces"] == [{"name": "Claude Code", "percent": 100}]
    await usage.plan_usage()
    assert asked == ["tok"]  # a minute's cache
    await usage.plan_usage(refresh=True)
    assert len(asked) == 2


async def test_expired_token_is_never_used_falls_back_to_claude_copy(
    tmp_path, monkeypatch
):
    old = {"fetchedAtMs": 1_000_000, "utilization": {"seven_day": {"utilization": 40}}}
    _claude_home(tmp_path, monkeypatch, -60, cached=old)

    async def no_fetch(token):
        raise AssertionError("an expired token must not be sent")

    monkeypatch.setattr(usage, "_fetch", no_fetch)
    got = await usage.plan_usage()
    assert not got["live"] and got["as_of"] == 1000.0
    assert [(lim["label"], lim["percent"]) for lim in got["limits"]] == [
        ("Неделя · все модели", 40.0)
    ]
