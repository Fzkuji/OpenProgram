"""Speech to text for composer voice input (openprogram.interfaces.speech.stt)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.interfaces.speech import stt as voice


def _cfg(**values):
    return {"voice": values}


@pytest.fixture
def no_local(monkeypatch):
    monkeypatch.setattr(voice, "local_cli", lambda: None)


def test_settings_fall_back_to_auto_for_unknown_values():
    with patch("openprogram.state.config.read_config", return_value=_cfg(engine="nope", language="xx", model=3)):
        settings = voice.read_settings()
    assert settings == voice.VoiceSettings("auto", "auto", "")


def test_auto_prefers_openai_then_groq(monkeypatch, no_local):
    keys = {"openai": "sk-a", "groq": "gk"}
    monkeypatch.setattr(voice, "_provider_key", keys.get)
    with patch("openprogram.state.config.read_config", return_value={}):
        assert voice.resolve_engine() == "openai"
        keys.pop("openai")
        assert voice.resolve_engine() == "groq"
        keys.pop("groq")
        assert voice.resolve_engine() is None


def test_auto_prefers_a_local_model_that_is_already_downloaded(monkeypatch, tmp_path):
    monkeypatch.setattr(voice, "_provider_key", lambda _: "sk")
    monkeypatch.setattr(voice, "local_cli", lambda: "/usr/bin/whisper-cli")
    model = tmp_path / "ggml-large-v3-turbo-q5_0.bin"
    monkeypatch.setattr(voice, "local_model_path", lambda name: model)
    with patch("openprogram.state.config.read_config", return_value={}):
        assert voice.resolve_engine() == "openai"
        model.write_bytes(b"x")
        assert voice.resolve_engine() == "local"


def test_model_override_applies_only_to_its_engine():
    chosen = voice.VoiceSettings(engine="groq", model="distil-whisper")
    assert voice.model_for("groq", chosen) == "distil-whisper"
    assert voice.model_for("openai", chosen) == voice.DEFAULT_MODELS["openai"]
    assert voice.model_for("openai", voice.VoiceSettings(model="x")) == voice.DEFAULT_MODELS["openai"]


def test_transcribe_posts_wav_to_the_openai_endpoint(monkeypatch, no_local):
    monkeypatch.setattr(voice, "_provider_key", lambda pid: "sk-test" if pid == "openai" else None)
    sent = {}

    class Response:
        status_code = 200

        def json(self):
            return {"text": " 你好 "}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, *, headers, data, files):
            sent.update(url=url, headers=headers, data=data, files=files)
            return Response()

    def fake_safe_client(consumer, *, timeout):
        sent.update(consumer=consumer, timeout=timeout)
        return FakeClient()

    # The request goes through the managed transport, never a bare httpx call.
    monkeypatch.setattr("openprogram.host.security.safe_http.safe_client", fake_safe_client)
    monkeypatch.setattr("httpx.post", None)
    with patch("openprogram.state.config.read_config", return_value=_cfg(language="zh")):
        result = voice.transcribe(b"RIFF....")
    assert result == {"text": "你好", "engine": "openai", "model": "gpt-4o-mini-transcribe"}
    assert sent["consumer"] == "tool.voice_api.fixed" and sent["timeout"] == 120
    assert sent["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert sent["headers"]["Authorization"] == "Bearer sk-test"
    assert sent["data"] == {"model": "gpt-4o-mini-transcribe", "response_format": "json", "language": "zh",
                            "prompt": voice._LANGUAGE_PROMPTS["zh"]}
    assert sent["files"]["file"][2] == "audio/wav"


def test_transcribe_without_any_engine_explains_the_fix(monkeypatch, no_local):
    monkeypatch.setattr(voice, "_provider_key", lambda _: None)
    with patch("openprogram.state.config.read_config", return_value={}):
        with pytest.raises(voice.VoiceError, match="Settings"):
            voice.transcribe(b"RIFF")


def test_empty_audio_is_empty_text():
    assert voice.transcribe(b"")["text"] == ""


def test_routes_report_status_and_errors(monkeypatch, no_local):
    from openprogram.interfaces.server.routes.settings import voice as routes

    app = FastAPI()
    routes.register(app)
    client = TestClient(app)
    monkeypatch.setattr(voice, "_provider_key", lambda _: None)
    with patch("openprogram.state.config.read_config", return_value={}):
        status = client.get("/api/voice/status").json()
        assert status["resolved_engine"] is None
        assert [row["id"] for row in status["engines"]] == ["openai", "groq", "local"]
        failed = client.post("/api/voice/transcribe", content=b"RIFF", headers={"Content-Type": "audio/wav"})
    assert failed.status_code == 422
    assert "Settings" in failed.json()["error"]

    monkeypatch.setattr(voice, "transcribe", lambda audio: {"text": "hi", "engine": "openai", "model": "m"})
    ok = client.post("/api/voice/transcribe", content=b"RIFF", headers={"Content-Type": "audio/wav"})
    assert ok.json()["text"] == "hi"


def test_local_engine_asks_for_simplified_chinese(monkeypatch, tmp_path):
    model = tmp_path / "ggml-m.bin"
    model.write_bytes(b"x")
    monkeypatch.setattr(voice, "local_cli", lambda: "/usr/bin/whisper-cli")
    monkeypatch.setattr(voice, "local_model_path", lambda name: model)
    seen = {}

    def fake_run(argv, **_):
        seen["argv"] = argv

        class Done:
            returncode = 0
            stdout = "帮我整理一下\n"
            stderr = ""
        return Done()

    monkeypatch.setattr(voice.subprocess, "run", fake_run)
    with patch("openprogram.state.config.read_config", return_value=_cfg(engine="local", language="zh")):
        assert voice.transcribe(b"RIFF")["text"] == "帮我整理一下"
    argv = seen["argv"]
    assert argv[argv.index("-l") + 1] == "zh"
    assert argv[argv.index("--prompt") + 1] == voice._LANGUAGE_PROMPTS["zh"]


class _FakeStream:
    def __init__(self, chunks, *, status=200, length=None, pause=0.0):
        self.chunks, self.status_code, self.pause = chunks, status, pause
        self.headers = {} if length is None else {"content-length": str(length)}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self, _size):
        import time

        for chunk in self.chunks:
            time.sleep(self.pause)
            yield chunk


def test_concurrent_phrases_download_the_local_model_once(monkeypatch, tmp_path):
    import threading

    import httpx

    model = tmp_path / "models" / "ggml-tiny.bin"
    monkeypatch.setattr(voice, "local_model_path", lambda name: model)
    calls = []
    body = [b"a" * 10, b"b" * 10, b"c" * 10]

    def fake_stream(*args, **kwargs):
        calls.append(args)
        return _FakeStream(body, length=30, pause=0.02)

    monkeypatch.setattr(httpx, "stream", fake_stream)
    results, errors = [], []

    def worker():
        try:
            results.append(voice._download_local_model("tiny"))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(calls) == 1
    assert results == [model] * 5
    assert model.read_bytes() == b"".join(body)
    assert list(model.parent.glob("*.part")) == []


def test_a_short_model_download_is_rejected_and_leaves_nothing(monkeypatch, tmp_path):
    import httpx

    model = tmp_path / "ggml-tiny.bin"
    monkeypatch.setattr(voice, "local_model_path", lambda name: model)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStream([b"abc"], length=100))
    with pytest.raises(voice.VoiceError, match="incomplete"):
        voice._download_local_model("tiny")
    assert not model.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_an_unexpected_download_failure_is_a_voice_error(monkeypatch, tmp_path):
    import httpx

    model = tmp_path / "ggml-tiny.bin"
    monkeypatch.setattr(voice, "local_model_path", lambda name: model)

    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(httpx, "stream", broken)
    with pytest.raises(voice.VoiceError, match="disk full"):
        voice._download_local_model("tiny")
    assert list(tmp_path.glob("*.part")) == []
