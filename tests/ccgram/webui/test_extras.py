from types import SimpleNamespace

import pytest

from ccgram import quiet_threads
from ccgram.webui import extras, push
from ccgram.webui.service import WebUIError


@pytest.fixture(autouse=True)
def _reset():
    quiet_threads.reset_for_testing()
    extras.reset_for_testing()  # re-applies the default mode's turn-quiet
    yield
    quiet_threads.reset_for_testing()
    extras.reset_for_testing()


class FakeClient:
    def __init__(self):
        self.deleted = []

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


@pytest.fixture
def wired(monkeypatch):
    sent, pushed = [], []
    ids = iter(range(100, 200))

    async def fake_send(client, chat_id, text, **kw):
        sent.append(
            (chat_id, kw["message_thread_id"], text, kw["disable_notification"])
        )
        return SimpleNamespace(message_id=next(ids))

    import ccgram.handlers.messaging_pipeline.message_sender as ms

    monkeypatch.setattr(ms, "rate_limit_send_message", fake_send)
    monkeypatch.setattr(push, "notify_reply", lambda *a, **kw: pushed.append(a))

    async def no_prompt(_wid):
        return None

    monkeypatch.setattr(extras, "_answerable_prompt", no_prompt)
    monkeypatch.setattr(extras, "_targets", lambda wid: [(-100, 7), (None, -3)])
    monkeypatch.setattr(extras, "_title", lambda chat, tid: f"t{tid}")
    return sent, pushed


async def test_each_mode_stop_does_nothing(wired):
    sent, pushed = wired
    extras.set_notify_mode("each")
    await extras.on_stop("@1", FakeClient())
    assert sent == [] and pushed == []
    assert not quiet_threads.is_quiet(-100, 7)


async def test_done_is_the_default_and_only_the_final_reply_rings(wired):
    sent, pushed = wired
    assert extras.notify_mode() == "done"
    assert quiet_threads.is_quiet(-100, 7)  # turn messages go out silent ...
    with quiet_threads.final_reply():
        assert not quiet_threads.is_quiet(-100, 7)  # ... except the last reply
    extras.remember_reply(7, "Чат", "первый ответ")
    extras.remember_reply(7, "Чат", "итоговый ответ")
    client = FakeClient()
    await extras.on_attention("@1", "Approval needed: Bash", client)
    assert sent == [(-100, 7, "⏳ Approval needed: Bash", False)]
    await extras.on_stop("@1", client)
    assert len(sent) == 1  # no extra "done" message: the reply itself rang
    assert client.deleted == [(-100, 100)]  # the answered "⏳" ping goes away
    assert pushed[-2:] == [(7, "Чат", "итоговый ответ"), (-3, "t-3", "Готово")]


async def test_done_mode_respects_muted_topic_and_global_mute(wired):
    sent, pushed = wired
    extras.set_notify_mode("done")
    quiet_threads.set_mode(7, "off")
    quiet_threads.set_all_muted(True)
    await extras.on_stop("@1", FakeClient())
    assert sent == []  # Telegram muted (topic + globally) ...
    assert [p[0] for p in pushed] == [7, -3]  # ... web is its own channel


async def test_attention_rings_in_done_mode_and_pushes(wired):
    sent, pushed = wired
    extras.set_notify_mode("each")
    await extras.on_attention("@1", "Approval needed: Bash", FakeClient())
    assert sent == []  # "each": the interactive prompt itself rings
    assert [p[2] for p in pushed] == ["⏳ Approval needed: Bash"] * 2
    extras.set_notify_mode("done")
    await extras.on_attention("@1", "Approval needed: Bash", FakeClient())
    assert sent == [(-100, 7, "⏳ Approval needed: Bash", False)]


def test_browse_stays_inside_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "proj" / ".git").mkdir(parents=True)
    (home / ".hidden").mkdir()
    monkeypatch.setattr(extras, "_home", lambda: home.resolve())
    got = extras.browse(None)
    assert got["dirs"] == [{"name": "proj", "git": True}]
    assert got["parent"] is None
    assert extras.browse(str(home / "proj"))["git"] is True
    with pytest.raises(WebUIError):
        extras.browse(str(tmp_path))
    with pytest.raises(WebUIError):
        extras.browse(str(home / "missing"))
    assert extras.valid_cwd(None) is None


async def test_pane_status_counts_only_a_running_spinner():
    from unittest.mock import AsyncMock, patch

    sep = "─" * 60
    chrome = f"\n{sep}\n❯ \n{sep}\n  ⏵⏵ auto mode on (shift+tab to cycle)"
    panes = {
        "@1": "● Пишу ответ\n✻ Pondering… (12s · ↓ 1.2k tokens)" + chrome,
        "@2": "● Готово\n✻ Baked for 19s · done 9:42 PM" + chrome,
        "@3": "● Стих\n* Zesting… (52s · ↓ 4.3k tokens)\n  ⎿  Tip: Use /permissions"
        + chrome,
    }
    capture = AsyncMock(side_effect=lambda wid: panes[wid])
    with patch("ccgram.multiplexer.multiplexer", capture_pane=capture):
        assert await extras.pane_status("@1") == "Pondering… (12s · ↓ 1.2k tokens)"
        assert await extras.pane_status("@2") is None
        assert await extras.pane_status("@3") == "Zesting… (52s · ↓ 4.3k tokens)"


def _fake_modes(cycle, lag=0):
    """A window whose Shift+Tab walks ``cycle``; the line repaints ``lag`` reads late."""
    state = {"i": 0, "stale": 0, "presses": 0}

    async def read():
        if state["stale"]:
            state["stale"] -= 1
            return cycle[(state["i"] - 1) % len(cycle)]
        return cycle[state["i"]]

    async def press():
        state["presses"] += 1
        state["i"] = (state["i"] + 1) % len(cycle)
        state["stale"] = lag

    return read, press, state


async def test_walk_to_reaches_the_target_despite_slow_repaint(monkeypatch):
    monkeypatch.setattr(extras.asyncio, "sleep", _no_sleep)
    read, press, st = _fake_modes(["Def", "Edit", "Plan", "Auto"], lag=2)
    assert await extras._walk_to("Plan", read, press) == ("Plan", None)
    assert st["presses"] == 2


async def test_walk_to_missing_mode_comes_back_and_learns_the_cycle(monkeypatch):
    monkeypatch.setattr(extras.asyncio, "sleep", _no_sleep)
    read, press, st = _fake_modes(["Edit", "Plan", "Def"])
    mode, cycle = await extras._walk_to("YOLO", read, press)
    assert mode == "Edit" and cycle == ["Edit", "Plan", "Def"]
    assert st["presses"] == 3


async def _no_sleep(_seconds):
    return None


PERMISSION = "Bash command\n\n  rm -rf build\n\nDo you want to proceed?\n❯ 1. Yes\n  2. Yes, and don't ask again for rm commands\n  3. No, and tell Claude what to do differently (esc)"
WRITE = (
    "✻ Crunched for 5s · done 11:49 PM\n\n● Write(/tmp/x/perm-probe.txt)\n\n Create file\n /tmp/x/perm-probe.txt\n"
    "  1 тест\n Do you want to create perm-probe.txt?\n ❯ 1. Yes\n"
    "   2. Yes, and switch to accept edits (auto-approve file edits and common file\n"
    "      commands) for this session (shift+tab)\n   3. No\n Esc to cancel · Tab to amend"
)
PLAN = "Would you like to proceed?\n❯ 1. Yes, and auto-accept edits\n  2. Yes, and manually approve edits\n  3. No, keep planning"


def test_prompt_answers_pick_the_narrowest_yes():
    assert extras.prompt_answers("PermissionPrompt", PERMISSION) == {
        "allow": "1",
        "deny": "3",
    }
    assert extras.prompt_answers("ExitPlanMode", PLAN) == {"allow": "2", "deny": "3"}
    assert extras.prompt_answers("SelectionUI", WRITE) == {"allow": "1", "deny": "3"}
    assert (
        extras.prompt_answers("SelectionUI", "Pick a theme\n❯ 1. Yes\n  2. No") is None
    )
    older = WRITE.replace(
        "Crunched for 5s", "Baked for 9s"
    )  # screen text above changed
    assert extras.prompt_fingerprint("SelectionUI", older) == extras.prompt_fingerprint(
        "SelectionUI", WRITE
    )
    assert extras.prompt_answers("AskUserQuestion", PERMISSION) is None
    assert (
        extras.prompt_answers(
            "PermissionPrompt", "Do you want to proceed?\nEsc to cancel"
        )
        is None
    )
    moved = PERMISSION.replace("❯ 1.", "  1.").replace("  2. Yes,", "❯ 2. Yes,")
    assert extras.prompt_fingerprint(
        "PermissionPrompt", moved
    ) == extras.prompt_fingerprint("PermissionPrompt", PERMISSION)
    assert extras.prompt_fingerprint(
        "PermissionPrompt", PLAN
    ) != extras.prompt_fingerprint("PermissionPrompt", PERMISSION)


async def test_answer_only_while_the_same_prompt_is_on_screen(monkeypatch):
    from unittest.mock import AsyncMock, patch

    from ccgram.terminal_parser import InteractiveUIContent

    screen = {
        "prompt": InteractiveUIContent(content=PERMISSION, name="PermissionPrompt")
    }

    async def current(_wid):
        return screen["prompt"]

    monkeypatch.setattr(extras, "_current_prompt", current)
    monkeypatch.setattr(
        "ccgram.webui.service.find_binding", lambda tid: SimpleNamespace(window_id="@9")
    )
    fp = extras.prompt_fingerprint("PermissionPrompt", PERMISSION)
    send = AsyncMock(return_value=True)
    with patch("ccgram.multiplexer.multiplexer", send_keys=send):
        assert (await extras.answer_prompt(5, fp, "deny"))["key"] == "3"
        send.assert_awaited_once_with("@9", "3", enter=False, literal=True)
        screen["prompt"] = InteractiveUIContent(content=PLAN, name="ExitPlanMode")
        with pytest.raises(WebUIError) as changed:
            await extras.answer_prompt(5, fp, "allow")
        assert changed.value.status == 409
        screen["prompt"] = None
        with pytest.raises(WebUIError):
            await extras.answer_prompt(5, fp, "allow")
    assert send.await_count == 1
