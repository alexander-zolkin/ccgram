"""In-process event bus feeding the web UI's server-sent-events stream.

Publishers never block: each subscriber owns a bounded queue, and a subscriber
that falls behind gets a single ``resync`` marker instead of the overflow so
the browser reloads state rather than silently missing messages.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_QUEUE_SIZE = 500


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    @property
    def has_subscribers(self) -> bool:
        return bool(self._subscribers)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the backlog and ask the client to reload from history.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"type": "resync"})
                logger.debug("webui subscriber lagged; sent resync")


bus = EventBus()


def publish_new_message(msg: Any, active_users: list[tuple[Any, ...]]) -> None:
    """Fan a monitor ``NewMessage`` out to web subscribers, once per topic.

    Never raises: the Telegram delivery path calls this inline.
    """
    _push_reply(msg, active_users)
    if not bus.has_subscribers:
        return
    try:
        seen: set[tuple[int | None, int]] = set()
        for _user_id, window_id, thread_id, chat_id in active_users:
            if thread_id is None or (chat_id, thread_id) in seen:
                continue
            seen.add((chat_id, thread_id))
            bus.publish(
                {
                    "type": "message",
                    "thread_id": thread_id,
                    "window_id": window_id,
                    "role": msg.role,
                    "text": msg.text,
                    "content_type": msg.content_type,
                    "tool_name": msg.tool_name,
                    "tool_use_id": msg.tool_use_id,
                    "is_complete": msg.is_complete,
                }
            )
    except Exception:  # noqa: BLE001 — never break Telegram delivery
        logger.exception("webui publish failed")


def _push_reply(msg: Any, active_users: list[tuple[Any, ...]]) -> None:
    """Web Push for a finished reply — independent of open browser tabs."""
    if msg.role != "assistant" or msg.content_type != "text" or not msg.is_complete:
        return
    try:
        # Lazy: push / quiet_threads / topic names are leaves, only needed here.
        from .. import quiet_threads
        from ..handlers.status.topic_emoji import get_stored_topic_name
        from . import extras, push

        if not push.has_subscriptions() and extras.notify_mode() != "done":
            return
        done: set[int] = set()
        for _user_id, _window_id, thread_id, chat_id in active_users:
            if thread_id is None or thread_id in done:
                continue
            done.add(thread_id)
            if quiet_threads.mode(thread_id) == "off":
                continue
            title = (chat_id and get_stored_topic_name(chat_id, thread_id)) or "Кара"
            if extras.notify_mode() == "done":
                # Deferred: the Stop hook pushes the turn's last reply once.
                extras.remember_reply(thread_id, title, str(msg.text or ""))
            else:
                push.notify_reply(thread_id, title, str(msg.text or ""))
    except Exception:  # noqa: BLE001 — never break Telegram delivery
        logger.exception("webui push failed")


def publish_web_only_message(msg: Any) -> None:
    """Route a message from a session whose topic is gone to its web id."""
    if not bus.has_subscribers:
        return
    try:
        # Lazy: registry reads its JSON on first use; only needed here.
        from .registry import entries

        targets = [
            (0, e.get("window_id", ""), sid, None)
            for sid, e in entries().items()
            if e.get("session_id") == msg.session_id
        ]
        if targets:
            publish_new_message(msg, targets)
    except Exception:  # noqa: BLE001 — never break Telegram delivery
        logger.exception("webui web-only publish failed")
