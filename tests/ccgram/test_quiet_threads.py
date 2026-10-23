from ccgram import quiet_threads


def test_web_input_silences_until_telegram_input():
    quiet_threads.reset_for_testing()
    kw = {"message_thread_id": 7}
    assert "disable_notification" not in quiet_threads.apply(-100, dict(kw))
    quiet_threads.mark_web(-100, 7)
    assert quiet_threads.apply(-100, dict(kw))["disable_notification"] is True
    # another topic / chat unaffected, explicit value kept
    assert "disable_notification" not in quiet_threads.apply(
        -100, {"message_thread_id": 8}
    )
    assert (
        quiet_threads.apply(-100, {**kw, "disable_notification": False})[
            "disable_notification"
        ]
        is False
    )
    assert "disable_notification" not in quiet_threads.apply(-100, {})
    quiet_threads.mark_telegram(-100, 7)
    assert "disable_notification" not in quiet_threads.apply(-100, dict(kw))
    quiet_threads.mark_web(None, None)  # tolerated
    quiet_threads.reset_for_testing()


async def test_ptb_client_send_message_is_silenced():
    from ccgram.telegram_client import PTBTelegramClient

    calls = []

    class Bot:
        async def send_message(self, **kw):
            calls.append(kw)

    quiet_threads.reset_for_testing()
    quiet_threads.mark_web(-100, 7)
    client = PTBTelegramClient(Bot())
    await client.send_message(-100, "hi", message_thread_id=7)
    await client.send_message(-100, "hi", message_thread_id=9)
    assert calls[0]["disable_notification"] is True
    assert "disable_notification" not in calls[1]
    quiet_threads.reset_for_testing()


def test_explicit_mode_overrides_auto_rule():
    quiet_threads.reset_for_testing()
    kw = {"message_thread_id": 7}
    assert quiet_threads.mode(7) == "auto"
    quiet_threads.set_mode(7, "off")
    assert quiet_threads.apply(-100, dict(kw))["disable_notification"] is True
    quiet_threads.mark_web(-100, 7)
    quiet_threads.set_mode(7, "on")
    # "on" rings even while the topic is web-driven
    assert "disable_notification" not in quiet_threads.apply(-100, dict(kw))
    quiet_threads.set_mode(7, "auto")
    assert quiet_threads.mode(7) == "auto"
    assert quiet_threads.apply(-100, dict(kw))["disable_notification"] is True
    quiet_threads.load_modes({8: "off", 9: "bogus"})
    assert quiet_threads.mode(8) == "off" and quiet_threads.mode(9) == "auto"
    assert quiet_threads.mode(None) == "auto"
    quiet_threads.reset_for_testing()


def test_sound_modes_persist(tmp_path, monkeypatch):
    from ccgram.webui import prefs

    monkeypatch.setenv("CCGRAM_DIR", str(tmp_path))
    prefs.reset_for_testing()
    prefs.set_sound_mode(5, "off")
    prefs.set_sound_mode(6, "on")
    prefs.set_sound_mode(6, "auto")
    prefs.reset_for_testing()
    assert prefs.sound_modes() == {5: "off"}
    prefs.reset_for_testing()


def test_global_mute_keeps_topic_modes():
    quiet_threads.reset_for_testing()
    kw = {"message_thread_id": 7}
    quiet_threads.set_mode(9, "off")  # a topic muted on its own
    quiet_threads.set_mode(7, "on")
    quiet_threads.set_all_muted(True)
    assert quiet_threads.apply(-100, dict(kw))["disable_notification"] is True
    assert quiet_threads.mode(7) == "on" and quiet_threads.mode(9) == "off"
    quiet_threads.set_all_muted(False)
    assert "disable_notification" not in quiet_threads.apply(-100, dict(kw))
    # the separately muted topic is still muted after un-muting everything
    assert quiet_threads.apply(-100, {"message_thread_id": 9})["disable_notification"]
    quiet_threads.reset_for_testing()
    assert not quiet_threads.all_muted()


def test_final_reply_rings_through_turn_quiet_only():
    quiet_threads.reset_for_testing()
    kw = {"message_thread_id": 7}
    quiet_threads.set_turn_quiet(True)
    assert quiet_threads.apply(-100, dict(kw))["disable_notification"] is True
    with quiet_threads.final_reply():
        assert "disable_notification" not in quiet_threads.apply(-100, dict(kw))
        quiet_threads.mark_web(-100, 7)  # read in the browser: stays silent
        assert quiet_threads.apply(-100, dict(kw))["disable_notification"] is True
        quiet_threads.set_mode(7, "on")
        assert "disable_notification" not in quiet_threads.apply(-100, dict(kw))
        quiet_threads.set_all_muted(True)
        assert quiet_threads.apply(-100, dict(kw))["disable_notification"] is True
    with quiet_threads.final_reply(False):
        quiet_threads.set_all_muted(False)
        assert quiet_threads.apply(-100, dict(kw))["disable_notification"] is True
    quiet_threads.reset_for_testing()


def test_relay_modes_decide_what_reaches_telegram():
    quiet_threads.reset_for_testing()
    assert quiet_threads.relay() == "all"
    assert quiet_threads.relays_content("tool_use", False)
    quiet_threads.set_relay("final")
    assert not quiet_threads.relays_content("tool_use", False)
    assert not quiet_threads.relays_content("thinking", False)
    assert not quiet_threads.relays_content("text", False)
    assert quiet_threads.relays_content("text", True)
    quiet_threads.set_relay("off")
    assert not quiet_threads.relays_content("text", True)
    quiet_threads.set_relay("bogus")
    assert quiet_threads.relay() == "all"
    quiet_threads.reset_for_testing()
