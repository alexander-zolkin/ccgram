"""Kara Hub's Telegram module inside ccgram (the old web UI's remains).

CCGRAM-HOTFIX:webui. The browser UI moved out to Kara Hub (its own service).
What stays here is what Telegram and the hub still need: a small loopback API
the hub drives Telegram topics through (``server``, token-only, see
CCGRAM-HOTFIX:hub-api), the Telegram preferences set from the hub
(``prefs``), web-only sessions whose topic is gone (``registry``), the
notify-when-done pings and the topic probe (``extras``) and the auto-title
loop that names topics from Claude Code's own title (``autotitle``).

``transcript`` and ``agents`` are kept as the source kara-node vendors
(``kara-node/vendor.sh``); ccgram itself uses only ``transcript.first_exchange``.
"""

from .server import start_webui, stop_webui

__all__ = ["start_webui", "stop_webui"]
