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


def test_thread_gone_send_jumps_the_probe_queue():
    from ccgram.handlers.topics import topic_lifecycle as tl

    tl.reset_probe_schedule()
    bindings = [(1, -100, tid, f"@{tid}") for tid in range(1, 50)]
    # every topic probed recently except none due -> nothing selected
    now = 10_000.0
    for _u, c, t, _w in bindings:
        tl._probe_last_ts[(1, c, t)] = now - 1
    assert tl._due_probe_targets(bindings, now) == []
    tl.note_thread_gone(-100, 42)
    assert tl._due_probe_targets(bindings, now) == [(1, -100, 42, "@42")]
    tl.reset_probe_schedule()


async def test_suspected_topic_uses_send_probe(monkeypatch):
    from ccgram.handlers.topics import topic_lifecycle as tl

    tl.reset_probe_schedule()
    calls = []

    async def fake_exists(client, chat_id, thread_id):
        calls.append(("probe", chat_id, thread_id))
        return False

    async def fake_unbind(client, user_id, chat_id, thread_id, wid, adapter):
        calls.append(("unbind", thread_id, wid))

    adapter = object()
    monkeypatch.setattr(tl, "probe_topic_exists_by_send", fake_exists)
    monkeypatch.setattr(tl, "_unbind_deleted_topic", fake_unbind)
    assert await tl._probe_suspected(object(), 1, -100, 42, "@42", adapter) is False
    tl.note_thread_gone(-100, 42)
    assert await tl._probe_suspected(object(), 1, -100, 42, "@42", adapter) is True
    assert calls == [("probe", -100, 42), ("unbind", 42, "@42")]
    assert (-100, 42) not in tl._suspected_gone


def test_update_and_negative_ids_from_the_old_page():
    registry.detach(
        -1,
        "@9",
        name="n",
        cwd="/w",
        provider="claude",
        approval_mode="yolo",
        session_id="",
        transcript_path="",
    )
    registry.detach(
        42,
        "@7",
        name="n",
        cwd="/w",
        provider="claude",
        approval_mode="yolo",
        session_id="",
        transcript_path="",
    )
    assert sorted(registry.entries()) == [-1, 42]
    registry.update(-1, session_id="s1")
    assert registry.get(-1)["session_id"] == "s1"
    registry.update(-5, session_id="nobody")  # unknown id: nothing happens
    assert registry.get(-5) is None


def test_codex_models_from_cache(tmp_path, monkeypatch):
    import json as _json

    from ccgram import model_catalog

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert model_catalog.list_codex_models() == []
    (tmp_path / "models_cache.json").write_text(
        _json.dumps(
            {
                "models": [
                    {"slug": "hidden", "display_name": "H", "visibility": "hide"},
                    {
                        "slug": "gpt-6-luna",
                        "display_name": "GPT-6-Luna",
                        "visibility": "list",
                    },
                    {
                        "slug": "gpt-5.6-terra",
                        "display_name": "GPT-5.6-Terra",
                        "visibility": "list",
                    },
                ]
            }
        )
    )
    assert [m.id for m in model_catalog.list_codex_models()] == [
        "gpt-6-luna",
        "gpt-5.6-terra",
    ]
    assert model_catalog.default_codex_model() == "gpt-6-luna"
    (tmp_path / "config.toml").write_text(
        'model = "gpt-5.6-terra"\n[profiles.x]\nmodel = "nope"\n'
    )
    assert model_catalog.default_codex_model() == "gpt-5.6-terra"


def test_detach_records_origin(monkeypatch, tmp_path):
    _enable(monkeypatch)
    _stub_lookups(monkeypatch, _view(str(tmp_path)))
    assert registry.detach_from_topic(1, -100, 42, "@7") is True
    assert registry.get(42)["origin"] == "telegram"
    assert registry.detach_from_topic(1, -100, 43, "@8", origin="web") is True
    assert registry.get(43)["origin"] == "web"
    registry.update(42, origin="kept")
    assert registry.get(42)["origin"] == "kept"
