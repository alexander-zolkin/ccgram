"""Read a reply aloud: text to speech through an OpenAI-compatible endpoint.

Configured by ``CCGRAM_TTS_BASE_URL`` (e.g. a local speaches at
``http://127.0.0.1:8010/v1``), ``CCGRAM_TTS_MODEL`` and ``CCGRAM_TTS_VOICE``;
without a base URL the feature is off. The browser sends one sentence group
at a time and prefetches the next while the current one plays, so the first
words start in about a second; recent clips are kept for replay.
"""

from __future__ import annotations

import hashlib
import os
from http import HTTPStatus
from collections import OrderedDict

import aiohttp

from .service import WebUIError

MAX_CHARS = 1200
CACHE_CLIPS = 96
TIMEOUT_S = 60

_cache: OrderedDict[str, bytes] = OrderedDict()


def enabled() -> bool:
    return bool(os.getenv("CCGRAM_TTS_BASE_URL"))


async def speak(text: str) -> bytes:
    """MP3 for ``text`` (one chunk of a reply)."""
    text = " ".join(text.split())
    if not text:
        raise WebUIError("nothing to read")
    if len(text) > MAX_CHARS:
        raise WebUIError(f"at most {MAX_CHARS} characters per chunk")
    if not enabled():
        raise WebUIError("text to speech is not configured", 404)
    model = os.getenv("CCGRAM_TTS_MODEL", "")
    voice = os.getenv("CCGRAM_TTS_VOICE", "")
    key = hashlib.sha256(f"{model}|{voice}|{text}".encode()).hexdigest()
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    url = os.environ["CCGRAM_TTS_BASE_URL"].rstrip("/") + "/audio/speech"
    body = {"model": model, "voice": voice, "input": text, "response_format": "mp3"}
    try:
        async with (
            aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)
            ) as http,
            http.post(url, json=body) as resp,
        ):
            if resp.status != HTTPStatus.OK:
                detail = (await resp.text())[:200]
                raise WebUIError(f"speech failed: HTTP {resp.status} {detail}", 502)
            audio = await resp.read()
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise WebUIError(f"speech service unreachable: {exc}", 502) from None
    _cache[key] = audio
    while len(_cache) > CACHE_CLIPS:
        _cache.popitem(last=False)
    return audio


def reset_for_testing() -> None:
    _cache.clear()
