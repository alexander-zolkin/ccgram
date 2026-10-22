import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from ccgram.webui import tts
from ccgram.webui.service import WebUIError


@pytest.fixture
async def speech(monkeypatch):
    calls = []

    async def handler(request):
        body = await request.json()
        calls.append(body)
        if body["input"] == "boom":
            return web.Response(status=500, text="no model")
        return web.Response(
            body=b"ID3" + body["input"].encode(), content_type="audio/mpeg"
        )

    app = web.Application()
    app.router.add_post("/v1/audio/speech", handler)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setenv("CCGRAM_TTS_BASE_URL", str(server.make_url("/v1")))
    monkeypatch.setenv("CCGRAM_TTS_MODEL", "piper-ru")
    monkeypatch.setenv("CCGRAM_TTS_VOICE", "irina")
    tts.reset_for_testing()
    yield calls
    await server.close()


async def test_speak_proxies_and_caches(speech):
    assert await tts.speak("Привет,   Кара") == "ID3Привет, Кара".encode()
    assert await tts.speak("Привет, Кара") == "ID3Привет, Кара".encode()
    assert len(speech) == 1  # the replay came from the cache
    assert speech[0] == {
        "model": "piper-ru",
        "voice": "irina",
        "input": "Привет, Кара",
        "response_format": "mp3",
    }


async def test_speak_refuses_bad_input_and_reports_failures(speech, monkeypatch):
    with pytest.raises(WebUIError):
        await tts.speak("   ")
    with pytest.raises(WebUIError):
        await tts.speak("x" * (tts.MAX_CHARS + 1))
    with pytest.raises(WebUIError) as failed:
        await tts.speak("boom")
    assert failed.value.status == 502
    monkeypatch.delenv("CCGRAM_TTS_BASE_URL")
    assert not tts.enabled()
