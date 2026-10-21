"""Web UI — a browser front-end over the same sessions the Telegram bot drives.

CCGRAM-HOTFIX:webui. Telegram stays the primary surface: every web session is
a forum topic, input typed in the browser reaches the topic through the
regular non-Telegram-origin relay, and sessions created here open a new topic.

Sub-modules:
- ``bus``: in-process pub/sub that ``message_routing`` publishes into
- ``auth``: password login + HMAC-signed session cookie
- ``service``: session list / history / send / create on top of ccgram state
- ``server``: aiohttp routes, SSE stream and lifecycle helpers
"""

from .bus import bus
from .server import start_webui, stop_webui

__all__ = ["bus", "start_webui", "stop_webui"]
