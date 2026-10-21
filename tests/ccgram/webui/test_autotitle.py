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
