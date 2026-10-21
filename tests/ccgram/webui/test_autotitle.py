import json

from ccgram.webui import autotitle


def test_placeholder_names():
    for name in ("kara 10-22 01-23", "веб 10-22 01-23", "workspace", "workspace-24"):
        assert autotitle.is_placeholder(name), name
    for name in ("Финансы", "kara", "kara 10-22", "workspace-x"):
        assert not autotitle.is_placeholder(name), name


def test_find_ai_title_takes_newest(tmp_path, monkeypatch):
    p = tmp_path / "t.jsonl"
    lines = [
        {"type": "user", "message": {"content": "hi"}},
        {"type": "ai-title", "aiTitle": "Первое"},
        {"type": "assistant", "message": {"content": []}},
        {"type": "ai-title", "aiTitle": "  Итоговое название  "},
    ]
    p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines))
    assert autotitle.find_ai_title(p) == "Итоговое название"
    empty = tmp_path / "e.jsonl"
    empty.write_text('{"type": "user"}\n')
    assert autotitle.find_ai_title(empty) is None
    assert autotitle.find_ai_title(tmp_path / "missing.jsonl") is None


def test_clean_title():
    c = autotitle._clean_title
    assert c("«Проверка связи».") == "Проверка связи"
    assert c("Название: Белые списки") == "Белые списки"
    assert c("\n\n  Деплой веба  \nлишнее") == "Деплой веба"
    long = "Белые списки: почему не открываются 158.160.90.231 и 10.0.0.1 вообще"
    assert c(long) == "Белые списки: почему не открываются 158.160.90.231"
    assert c("x") is None
    assert c("y" * 200) is None


async def test_generate_title_needs_command(tmp_path, monkeypatch):
    monkeypatch.delenv("CCGRAM_WEBUI_TITLE_CMD", raising=False)
    assert await autotitle.generate_title(tmp_path / "x.jsonl") is None


def test_first_exchange(tmp_path):
    from ccgram.webui import transcript

    p = tmp_path / "t.jsonl"
    rows = [
        {"type": "user", "uuid": "1", "message": {"content": "Test!!!"}},
        {
            "type": "assistant",
            "uuid": "2",
            "message": {"content": [{"type": "text", "text": "ok"}]},
        },
        {"type": "user", "uuid": "3", "message": {"content": "второй"}},
    ]
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    assert transcript.first_exchange(p) == (["Test!!!", "второй"], "ok")


def test_topic_gone_detection():
    assert autotitle._topic_gone(Exception("Bad Request: Topic_id_invalid"))
    assert autotitle._topic_gone(Exception("Message thread not found"))
    assert not autotitle._topic_gone(Exception("Flood control exceeded"))


async def test_tick_names_from_first_message_and_waits_quietly(tmp_path, monkeypatch):
    import time as _time
    from types import SimpleNamespace

    from ccgram.webui import service

    autotitle._done.clear()
    autotitle._failed.clear()
    path = tmp_path / "t.jsonl"
    path.write_text("")
    binding = SimpleNamespace(web_only=False, chat_id=-100, thread_id=5)
    sessions = [
        {"thread_id": 5, "name": "kara 10-22 02-27", "last_activity": _time.time()}
    ]
    renamed = []

    async def fake_list():
        return sessions

    async def fake_rename(bot, tid, title):
        renamed.append((tid, title))

    async def fake_generate(p):
        return "Проверка связи"

    monkeypatch.setattr(service, "list_sessions", fake_list)
    monkeypatch.setattr(service, "find_binding", lambda tid: binding)
    monkeypatch.setattr(service, "_transcript_for", lambda b: (path, "claude"))
    monkeypatch.setattr(service, "rename", fake_rename)
    monkeypatch.setattr(autotitle, "generate_title", fake_generate)
    monkeypatch.setattr(autotitle, "RENAME_GAP_S", 0)

    await autotitle._tick(None)  # no message yet: nothing, and not a failure
    assert renamed == [] and 5 not in autotitle._failed
    path.write_text(
        json.dumps({"type": "user", "uuid": "u", "message": {"content": "Test1"}})
        + "\n"
    )
    await autotitle._tick(None)  # named right away, no reply needed
    assert renamed == [(5, "Проверка связи")]
    autotitle._done.clear()
