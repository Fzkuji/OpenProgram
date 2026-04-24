"""Text-to-speech playback for CLI chat replies.

Minimal first pass: only the ``openai`` provider is actually wired.
Other providers the setup lists (elevenlabs, edge-tts, playht)
fall through to a ``[tts] not yet implemented`` notice so the user
sees exactly what's missing instead of a silent fail.

Usage:

    from openprogram.tts import speak
    speak("Hello world")          # no-op if tts.provider != 'openai'

Playback: writes the generated audio to a temp .mp3 and invokes a
platform-appropriate player (``afplay`` on macOS, ``mpg123`` / ``ffplay``
elsewhere). Runs in a background thread so the REPL doesn't block
while audio plays.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any


_WARNED_PROVIDERS: set[str] = set()


def _read_tts_cfg() -> dict[str, Any]:
    try:
        from openprogram.setup import _read_config
        cfg = _read_config()
    except Exception:
        return {}
    return cfg.get("tts", {}) or {}


def _api_key(env_name: str) -> str | None:
    """Env var first, config.json api_keys fallback."""
    v = os.environ.get(env_name)
    if v:
        return v
    try:
        from openprogram.setup import _read_config
        return (_read_config().get("api_keys", {}) or {}).get(env_name)
    except Exception:
        return None


def _find_player() -> list[str] | None:
    """Return argv prefix for a non-blocking mp3 player, or None."""
    for cmd in ("afplay", "mpg123", "ffplay", "mpv"):
        path = shutil.which(cmd)
        if not path:
            continue
        if cmd == "ffplay":
            return [path, "-nodisp", "-autoexit", "-loglevel", "quiet"]
        if cmd == "mpg123":
            return [path, "-q"]
        if cmd == "mpv":
            return [path, "--no-terminal"]
        return [path]  # afplay
    return None


def _play_file(path: str) -> None:
    argv = _find_player()
    if argv is None:
        print(f"[tts] no mp3 player found (install afplay/mpg123/ffplay); "
              f"audio written to {path}")
        return
    try:
        subprocess.Popen(argv + [path],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[tts] player failed: {e}")


def _openai_tts(text: str, cfg: dict[str, Any]) -> str | None:
    """Hit openai/v1/audio/speech; return .mp3 path or None on failure.

    Uses `requests` which is already a transitive dep; avoids pulling
    in the full `openai` client just for this.
    """
    key = _api_key(cfg.get("api_key_env") or "OPENAI_API_KEY")
    if not key:
        print("[tts] OPENAI_API_KEY missing — run `openprogram config tts`.")
        return None
    try:
        import requests
    except ImportError:
        print("[tts] `requests` not installed; cannot reach OpenAI TTS.")
        return None

    voice = cfg.get("voice") or "alloy"
    model = cfg.get("model") or "tts-1"
    url = cfg.get("base_url") or "https://api.openai.com/v1/audio/speech"
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"model": model, "voice": voice, "input": text,
                  "response_format": "mp3"},
            timeout=30,
        )
    except Exception as e:
        print(f"[tts] request failed: {e}")
        return None
    if r.status_code != 200:
        print(f"[tts] OpenAI returned {r.status_code}: "
              f"{r.text[:200]}")
        return None
    fd, path = tempfile.mkstemp(prefix="op-tts-", suffix=".mp3")
    with os.fdopen(fd, "wb") as f:
        f.write(r.content)
    return path


def _elevenlabs_tts(text: str, cfg: dict[str, Any]) -> str | None:
    """ElevenLabs TTS via their v1 text-to-speech endpoint.

    Config slots honoured:
        voice       ElevenLabs voice id (default: 'Rachel' common id)
        model_id    ElevenLabs model id (default: 'eleven_turbo_v2')
    """
    key = _api_key(cfg.get("api_key_env") or "ELEVENLABS_API_KEY")
    if not key:
        print("[tts] ELEVENLABS_API_KEY missing — run `openprogram config tts`.")
        return None
    try:
        import requests
    except ImportError:
        print("[tts] `requests` not installed; cannot reach ElevenLabs.")
        return None

    # "Rachel" is ElevenLabs' classic default voice; users override via
    # config if they want a different one.
    voice_id = cfg.get("voice") or "21m00Tcm4TlvDq8ikWAM"
    model_id = cfg.get("model_id") or "eleven_turbo_v2"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    try:
        r = requests.post(
            url,
            headers={
                "xi-api-key": key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "text": text,
                "model_id": model_id,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
            timeout=30,
        )
    except Exception as e:
        print(f"[tts] request failed: {e}")
        return None
    if r.status_code != 200:
        print(f"[tts] ElevenLabs returned {r.status_code}: "
              f"{r.text[:200]}")
        return None
    fd, path = tempfile.mkstemp(prefix="op-tts-", suffix=".mp3")
    with os.fdopen(fd, "wb") as f:
        f.write(r.content)
    return path


def _edge_tts(text: str, cfg: dict[str, Any]) -> str | None:
    """Microsoft Edge online TTS via the ``edge-tts`` package.

    Free, no API key, uses MS's public voice WebSocket. Voices like
    ``en-US-AriaNeural`` / ``zh-CN-XiaoxiaoNeural``.
    """
    try:
        import edge_tts  # type: ignore
    except ImportError:
        print("[tts] `edge-tts` not installed. Install with: "
              "pip install edge-tts")
        return None
    import asyncio

    voice = cfg.get("voice") or "en-US-AriaNeural"
    fd, path = tempfile.mkstemp(prefix="op-tts-", suffix=".mp3")
    os.close(fd)

    async def _gen() -> None:
        comm = edge_tts.Communicate(text, voice)
        await comm.save(path)

    try:
        try:
            asyncio.run(_gen())
        except RuntimeError:
            # Event loop already exists in this thread (rare — we're in
            # the daemon thread, but be defensive). Use a fresh loop.
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_gen())
            finally:
                loop.close()
    except Exception as e:
        print(f"[tts] edge-tts failed: {type(e).__name__}: {e}")
        try:
            os.unlink(path)
        except OSError:
            pass
        return None
    return path


def speak(text: str) -> None:
    """Speak ``text`` if a TTS provider is configured; no-op otherwise.

    Non-blocking — audio generation + playback run in a background
    thread so the REPL stays responsive. Errors print a short
    ``[tts] ...`` line and move on.
    """
    if not text or not text.strip():
        return
    cfg = _read_tts_cfg()
    provider = (cfg.get("provider") or "none").lower()
    if provider in ("", "none"):
        return

    def _worker() -> None:
        if provider == "openai":
            path = _openai_tts(text, cfg)
        elif provider == "elevenlabs":
            path = _elevenlabs_tts(text, cfg)
        elif provider == "edge-tts":
            path = _edge_tts(text, cfg)
        else:
            if provider in _WARNED_PROVIDERS:
                return
            _WARNED_PROVIDERS.add(provider)
            print(f"[tts] provider {provider!r} is not yet implemented "
                  f"(config is stored). Pick openai / elevenlabs / "
                  f"edge-tts / none with `openprogram config tts`.")
            return
        if path:
            _play_file(path)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
