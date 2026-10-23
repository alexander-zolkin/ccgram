"""A new web chat doesn't wait for the topic's cosmetic Telegram calls."""

import asyncio
from types import SimpleNamespace

from ccgram.webui import service


class _SlowBot:
    """Every call waits for ``gate``, like a drained Bot API budget."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.log: list[tuple] = []

    async def send_message(self, **kwargs):
        await self.gate.wait()
        self.log.append(("send", kwargs["text"]))
        return SimpleNamespace(edit_text=self._edit)

    async def _edit(self, text, **_kwargs):
        self.log.append(("edit", text))

    async def edit_forum_topic(self, **kwargs):
        await self.gate.wait()
        self.log.append(("rename", kwargs["name"]))
        return True

    def get_me(self):
        return "me"


async def test_status_post_and_edits_run_in_background_in_order():
    bot = _SlowBot()
    query = service._LaunchQuery(bot, -100, 5)  # type: ignore[arg-type]
    assert query.message.chat.id == -100
    assert query.message.chat.type == "supergroup"
    await asyncio.wait_for(query.edit_message_text("✅ one"), 0.1)
    await asyncio.wait_for(query.edit_message_text("✅ two"), 0.1)
    launch_bot = service._LaunchBot(bot)  # type: ignore[arg-type]
    assert await asyncio.wait_for(launch_bot.edit_forum_topic(name="t"), 0.1)
    assert launch_bot.get_me() == "me"
    assert bot.log == []
    bot.gate.set()
    for _ in range(20):
        await asyncio.sleep(0)
    assert ("rename", "t") in bot.log
    post = [e for e in bot.log if e[0] != "rename"]
    assert post == [
        ("send", "🌐 Starting a session from the web…"),
        ("edit", "✅ one"),
        ("edit", "✅ two"),
    ]
