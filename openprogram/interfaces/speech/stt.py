"""Speech to text for the composer's voice input.

The browser records 16 kHz mono WAV, cuts it at pauses and posts each
phrase to ``POST /api/voice/transcribe``; this module turns one phrase
into text with the engine chosen in Settings → Voice. The Meetings
application transcribes its 30-second chunks through
:func:`transcribe_segments`, which keeps each phrase's time.

Engines (one function each, same signature):

* ``openai`` — ``/v1/audio/transcriptions`` with the OpenAI provider key.
* ``groq``   — Groq's OpenAI-compatible transcription endpoint (Whisper).
* ``local``  — whisper.cpp's ``whisper-cli`` on this computer. Audio never
  leaves the machine. The ggml model is downloaded into the state
  directory on first use.

``auto`` picks a local engine whose model is already downloaded, then
OpenAI, then Groq, then a local engine that still has to download.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openprogram.runtime.speech_link import TranscriptionError
from openprogram.host._platform.filesystem import replace_file

ENGINES = ("openai", "groq", "local")
LANGUAGES = ("auto", "zh", "en", "ja", "ko", "fr", "de", "es")

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini-transcribe",
    "groq": "whisper-large-v3-turbo",
    "local": "large-v3-turbo-q5_0",
}

_API_BASES = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
}

# One phrase is a few seconds; a 10-minute cap keeps a stuck recording
# from posting hundreds of megabytes.
MAX_AUDIO_BYTES = 20 * 1024 * 1024

# Whisper writes Chinese in traditional characters unless the context
# leans simplified; a short prompt in the target script fixes that.
_LANGUAGE_PROMPTS = {"zh": "以下是普通话的句子，使用简体中文。"}

_LOCAL_CLI_NAMES = ("whisper-cli", "whisper-cpp")
_LOCAL_CLI_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")
_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{model}.bin"


class VoiceError(TranscriptionError):
    """A failure the composer shows to the user as is."""


@dataclass(frozen=True)
class VoiceSettings:
    engine: str = "auto"
    language: str = "auto"
    model: str = ""


def model_for(engine: str, settings: VoiceSettings) -> str:
    """The model override applies only to the engine it was chosen for."""
    if settings.engine == engine and settings.model:
        return settings.model
    return DEFAULT_MODELS[engine]


def read_settings() -> VoiceSettings:
    try:
        from openprogram.state.config import read_config
        cfg = (read_config().get("voice") or {})
    except Exception:
        cfg = {}
    engine = cfg.get("engine") if cfg.get("engine") in ("auto", *ENGINES) else "auto"
    language = cfg.get("language") if cfg.get("language") in LANGUAGES else "auto"
    model = cfg.get("model") if isinstance(cfg.get("model"), str) else ""
    return VoiceSettings(engine=engine, language=language, model=model.strip())


# ---------------------------------------------------------------- engines


def _provider_key(provider_id: str) -> str | None:
    try:
        from openprogram.models.providers.settings.env_api_keys import resolve_provider_key
        return resolve_provider_key(provider_id)
    except Exception:
        return None


def local_cli() -> str | None:
    """Path of whisper.cpp's command-line tool, or None."""
    for name in _LOCAL_CLI_NAMES:
        found = shutil.which(name)
        if found:
            return found
        for folder in _LOCAL_CLI_DIRS:
            candidate = Path(folder) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def local_model_path(model: str) -> Path:
    from openprogram.state.paths import get_state_dir
    return get_state_dir() / "voice" / "models" / f"ggml-{model}.bin"


_DOWNLOAD_LOCKS: dict[str, threading.Lock] = {}
_DOWNLOAD_LOCKS_GUARD = threading.Lock()


def _download_lock(model: str) -> threading.Lock:
    with _DOWNLOAD_LOCKS_GUARD:
        return _DOWNLOAD_LOCKS.setdefault(model, threading.Lock())


def _download_local_model(model: str) -> Path:
    """The model file, downloading it once even when phrases arrive together.

    One lock per model: the first caller downloads into its own temporary
    file and moves it into place; the others wait and find it there.
    """
    target = local_model_path(model)
    if target.is_file() and target.stat().st_size > 0:
        return target
    with _download_lock(model):
        if target.is_file() and target.stat().st_size > 0:
            return target
        try:
            _fetch_model(model, target)
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceError(f"Could not download the Whisper model {model}: {exc}") from exc
    return target


def _fetch_model(model: str, target: Path) -> None:
    import httpx

    target.parent.mkdir(parents=True, exist_ok=True)
    fh = tempfile.NamedTemporaryFile(dir=target.parent, prefix=f"{target.name}.",
                                     suffix=".part", delete=False)
    partial = Path(fh.name)
    try:
        with fh:
            try:
                with httpx.stream("GET", _MODEL_URL.format(model=model),
                                  follow_redirects=True, timeout=120) as response:
                    if response.status_code != 200:
                        raise VoiceError(f"Could not download the Whisper model {model} "
                                         f"(HTTP {response.status_code}).")
                    # Content-Length counts encoded bytes; compare only unencoded bodies.
                    encoded = response.headers.get("content-encoding", "identity") != "identity"
                    expected = None if encoded else response.headers.get("content-length")
                    written = 0
                    for chunk in response.iter_bytes(1 << 20):
                        fh.write(chunk)
                        written += len(chunk)
            except httpx.HTTPError as exc:
                raise VoiceError(f"Could not download the Whisper model {model}: {exc}") from exc
        if expected and expected.isdigit() and written != int(expected):
            raise VoiceError(f"The Whisper model {model} download was incomplete "
                             f"({written} of {expected} bytes). Try again.")
        if written == 0:
            raise VoiceError(f"The Whisper model {model} download was empty. Try again.")
        replace_file(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def _run_local(audio: bytes, *, model: str, language: str, timed: bool) -> str:
    """whisper.cpp's output for one WAV file: plain text, or ``[from --> to]`` lines."""
    cli = local_cli()
    if not cli:
        raise VoiceError("whisper.cpp is not installed. Install it with "
                         "`brew install whisper-cpp`, or pick another engine in Settings → Voice.")
    model_file = _download_local_model(model)
    with tempfile.TemporaryDirectory(prefix="openprogram-voice-") as tmp:
        wav = Path(tmp) / "phrase.wav"
        wav.write_bytes(audio)
        argv = [cli, "-m", str(model_file), "-f", str(wav), "-np", "-l", language]
        if not timed:
            argv.append("-nt")
        if language in _LANGUAGE_PROMPTS:
            argv += ["--prompt", _LANGUAGE_PROMPTS[language]]
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired as exc:
            raise VoiceError("Local transcription took longer than 5 minutes.") from exc
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["no output"]
        raise VoiceError(f"whisper.cpp failed: {tail[0]}")
    return done.stdout


def _transcribe_local(audio: bytes, *, model: str, language: str) -> str:
    out = _run_local(audio, model=model, language=language, timed=False)
    return " ".join(line.strip() for line in out.splitlines() if line.strip())


_TIMED_LINE = re.compile(
    r"^\[(\d+):(\d+):(\d+)[.,](\d+)\s*-->\s*(\d+):(\d+):(\d+)[.,](\d+)\]\s*(.*)$")


def _ms(h: str, m: str, s: str, frac: str) -> int:
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int((frac + "000")[:3])


def parse_timed_lines(output: str) -> list[dict[str, Any]]:
    """whisper.cpp's ``[00:00:01.000 --> 00:00:04.200]  text`` lines as segments."""
    segments = []
    for line in output.splitlines():
        found = _TIMED_LINE.match(line.strip())
        if not found:
            continue
        text = found.group(9).strip()
        if text:
            segments.append({"start_ms": _ms(*found.group(1, 2, 3, 4)),
                             "end_ms": _ms(*found.group(5, 6, 7, 8)), "text": text})
    return segments


def _segments_local(audio: bytes, *, model: str, language: str) -> list[dict[str, Any]]:
    return parse_timed_lines(_run_local(audio, model=model, language=language, timed=True))


def wav_duration_ms(audio: bytes) -> int:
    """Length of a WAV recording, or 0 when it is not one."""
    import io
    import wave
    try:
        with wave.open(io.BytesIO(audio)) as reader:
            return int(reader.getnframes() * 1000 / max(1, reader.getframerate()))
    except (wave.Error, EOFError):
        return 0


def _request_http(engine: str, audio: bytes, *, model: str, language: str, response_format: str) -> dict:
    import httpx

    key = _provider_key(engine)
    if not key:
        name = "OpenAI" if engine == "openai" else "Groq"
        raise VoiceError(f"No {name} API key. Add one in Settings → Providers, "
                         "or pick another engine in Settings → Voice.")
    data: dict[str, Any] = {"model": model, "response_format": response_format}
    if language != "auto":
        data["language"] = language
    if language in _LANGUAGE_PROMPTS:
        data["prompt"] = _LANGUAGE_PROMPTS[language]
    from openprogram.host.security.safe_http import safe_client
    from openprogram.host.security.url_policy import URLPolicyError

    try:
        with safe_client("tool.voice_api.fixed", timeout=120) as client:
            response = client.post(
                f"{_API_BASES[engine]}/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                data=data,
                files={"file": ("phrase.wav", audio, "audio/wav")},
            )
    except (httpx.HTTPError, URLPolicyError) as exc:
        raise VoiceError(f"Transcription request failed: {exc}") from exc
    if response.status_code != 200:
        try:
            detail = response.json().get("error", {}).get("message") or response.text
        except Exception:
            detail = response.text
        raise VoiceError(f"Transcription failed (HTTP {response.status_code}): {detail[:300]}")
    return response.json()


def _transcribe_http(engine: str, audio: bytes, *, model: str, language: str) -> str:
    body = _request_http(engine, audio, model=model, language=language, response_format="json")
    return str(body.get("text") or "").strip()


def _segments_http(engine: str, audio: bytes, *, model: str, language: str) -> list[dict[str, Any]]:
    """Whisper models answer ``verbose_json`` with timed segments; the
    ``gpt-4o-*-transcribe`` models only with text, which becomes one segment."""
    if model.startswith("whisper"):
        body = _request_http(engine, audio, model=model, language=language, response_format="verbose_json")
        segments = [{"start_ms": int(float(row.get("start") or 0) * 1000),
                     "end_ms": int(float(row.get("end") or 0) * 1000),
                     "text": str(row.get("text") or "").strip()}
                    for row in body.get("segments") or []]
        segments = [row for row in segments if row["text"]]
        text = str(body.get("text") or "").strip()
        if segments or not text:
            return segments
    else:
        text = _transcribe_http(engine, audio, model=model, language=language)
    return [{"start_ms": 0, "end_ms": wav_duration_ms(audio), "text": text}] if text else []


_RUNNERS: dict[str, Callable[..., str]] = {
    "openai": lambda audio, **kw: _transcribe_http("openai", audio, **kw),
    "groq": lambda audio, **kw: _transcribe_http("groq", audio, **kw),
    "local": _transcribe_local,
}

_SEGMENT_RUNNERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "openai": lambda audio, **kw: _segments_http("openai", audio, **kw),
    "groq": lambda audio, **kw: _segments_http("groq", audio, **kw),
    "local": _segments_local,
}


# ---------------------------------------------------------------- selection


def engine_status(settings: VoiceSettings | None = None) -> list[dict[str, Any]]:
    """Per-engine availability, in the order ``auto`` tries them."""
    settings = settings or read_settings()
    cli = local_cli()
    local_model = model_for("local", settings)
    model_ready = local_model_path(local_model).is_file()
    return [
        {"id": "openai", "available": bool(_provider_key("openai")),
         "detail": "API key set" if _provider_key("openai") else "no API key"},
        {"id": "groq", "available": bool(_provider_key("groq")),
         "detail": "API key set" if _provider_key("groq") else "no API key"},
        {"id": "local", "available": bool(cli),
         "detail": ("model ready" if model_ready else f"downloads ggml-{local_model}.bin on first use")
         if cli else "whisper.cpp not installed", "model_ready": model_ready},
    ]


def resolve_engine(settings: VoiceSettings | None = None) -> str | None:
    settings = settings or read_settings()
    if settings.engine != "auto":
        return settings.engine
    status = {row["id"]: row for row in engine_status(settings)}
    if status["local"]["available"] and status["local"]["model_ready"]:
        return "local"
    for engine in ("openai", "groq", "local"):
        if status[engine]["available"]:
            return engine
    return None


def status() -> dict[str, Any]:
    settings = read_settings()
    engine = resolve_engine(settings)
    return {
        "engine": settings.engine,
        "language": settings.language,
        "model": settings.model,
        "resolved_engine": engine,
        "resolved_model": model_for(engine, settings) if engine else "",
        "engines": engine_status(settings),
        "default_models": DEFAULT_MODELS,
    }


def _engine_for(audio: bytes) -> tuple[str, str, VoiceSettings]:
    if len(audio) > MAX_AUDIO_BYTES:
        raise VoiceError("The recording is too long to transcribe in one piece.")
    settings = read_settings()
    engine = resolve_engine(settings)
    if engine is None:
        raise VoiceError("No speech engine is set up. Add an OpenAI or Groq key in "
                         "Settings → Providers, or install whisper.cpp.")
    return engine, model_for(engine, settings), settings


def transcribe(audio: bytes) -> dict[str, str]:
    """Text of one recorded phrase, with the engine and model that made it."""
    if not audio:
        return {"text": "", "engine": "", "model": ""}
    engine, model, settings = _engine_for(audio)
    text = _RUNNERS[engine](audio, model=model, language=settings.language)
    return {"text": text, "engine": engine, "model": model}


def transcribe_segments(audio: bytes, language: str = "auto") -> dict[str, Any]:
    """Timed segments of one WAV recording (a meeting chunk), with the
    engine and model that made them: ``{"segments": [{"start_ms",
    "end_ms", "text"}], "engine", "model"}``.

    The language is the caller's, not Settings → Voice's: a meeting
    detects it (``auto``) unless one was given when it started, since a
    voice-input language forced onto a call in another language makes
    Whisper invent text in the wrong one.
    """
    if not audio:
        return {"segments": [], "engine": "", "model": ""}
    engine, model, _settings = _engine_for(audio)
    language = language if re.fullmatch(r"[a-z]{2,3}", language or "") else "auto"
    segments = _SEGMENT_RUNNERS[engine](audio, model=model, language=language)
    return {"segments": segments, "engine": engine, "model": model}
