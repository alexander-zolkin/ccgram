from types import SimpleNamespace

import pytest

from ccgram.webui import registry


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGRAM_DIR", str(tmp_path))
    registry.reset_for_testing()
    yield
    registry.reset_for_testing()


def _enable(monkeypatch, on=True):
    from ccgram.config import config

    monkeypatch.setattr(config, "webui_port", 8790 if on else 0, raising=False)
    monkeypatch.setattr(config, "webui_password", "pw" if on else "", raising=False)


def _view(cwd, sid="sid-1", tx="/tmp/t.jsonl"):
    return SimpleNamespace(
        cwd=cwd,
        session_id=sid,
        transcript_path=tx,
        provider_name="claude",
        approval_mode="yolo",
    )


def _stub_lookups(monkeypatch, view):
    from ccgram import window_query
    from ccgram.handlers.status import topic_emoji
    from ccgram.thread_router import thread_router
    from ccgram.webui import service

    monkeypatch.setattr(window_query, "view_window", lambda wid: view)
    monkeypatch.setattr(service, "_event_index", lambda: {})
    monkeypatch.setattr(topic_emoji, "get_stored_topic_name", lambda c, t: "Финансы")
    monkeypatch.setattr(
        type(thread_router), "__getattr__", lambda self, n: lambda *a, **k: -100
    )


def test_roundtrip_persists_across_reload(tmp_path):
    registry.detach(
        42,
        "@7",
        name="n",
        cwd="/w",
        provider="claude",
        approval_mode="yolo",
        session_id="s",
        transcript_path="/t",
    )
    registry.reset_for_testing()
    assert registry.get(42)["window_id"] == "@7"
    assert registry.is_web_only("@7")
    assert registry.web_only_window_ids() == {"@7"}
    registry.set_window(42, "@9")
    assert registry.is_web_only("@9") and not registry.is_web_only("@7")
    assert registry.remove(42)["window_id"] == "@9"
    assert registry.entries() == {}


def test_detach_is_off_when_webui_disabled(monkeypatch, tmp_path):
    _enable(monkeypatch, on=False)
    _stub_lookups(monkeypatch, _view(str(tmp_path)))
    assert registry.detach_from_topic(1, -100, 42, "@7") is False
    assert registry.entries() == {}


def test_detach_keeps_session(monkeypatch, tmp_path):
    _enable(monkeypatch)
    _stub_lookups(monkeypatch, _view(str(tmp_path)))
    assert registry.detach_from_topic(1, -100, 42, "@7") is True
    entry = registry.get(42)
    assert entry["window_id"] == "@7"
    assert entry["session_id"] == "sid-1"
    assert entry["name"] == "Финансы"
    assert entry["approval_mode"] == "yolo"


def test_detach_refuses_private_sessions(monkeypatch, tmp_path):
    _enable(monkeypatch)
    private = tmp_path / "private"
    (private / "s1").mkdir(parents=True)
    monkeypatch.setenv("CCGRAM_PRIVATE_DIR", str(private))
    _stub_lookups(monkeypatch, _view(str(private / "s1")))
    assert registry.detach_from_topic(1, -100, 42, "@7") is False
    assert registry.entries() == {}


def test_detach_needs_a_session_id(monkeypatch, tmp_path):
    _enable(monkeypatch)
    _stub_lookups(monkeypatch, _view(str(tmp_path), sid=""))
    assert registry.detach_from_topic(1, -100, 42, "@7") is False


async def test_new_window_for_web_only_session_gets_no_topic(monkeypatch):
    from ccgram.handlers.topics import topic_orchestration
    from ccgram.session_monitor import NewWindowEvent

    registry.detach(
        42,
        "@7",
        name="n",
        cwd="/w",
        provider="claude",
        approval_mode="yolo",
        session_id="s",
        transcript_path="/t",
    )
    monkeypatch.setattr(
        topic_orchestration, "_is_window_already_bound", lambda w: False
    )

    async def boom(*a, **k):
        raise AssertionError("must not create a topic for a web-only window")

    monkeypatch.setattr(topic_orchestration, "create_topic_in_chat", boom)
    event = NewWindowEvent(window_id="@7", session_id="s", window_name="n", cwd="/w")
    assert await topic_orchestration.handle_new_window(event, object()) is False
