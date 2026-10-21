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
