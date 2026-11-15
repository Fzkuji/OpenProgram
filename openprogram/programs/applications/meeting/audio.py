"""Meeting audio: ffmpeg conversion and the silence check.

The page records the microphone as WebM/Opus chunks (or Ogg/MP4, as the
browser can); the server records the computer's sound as WAV chunks.
Speech to text takes 16 kHz mono 16-bit WAV: a chunk already in that form
is used as it is, anything else goes through ``ffmpeg``, which reads the
format from the data, not the file name. One chunk is converted at a
time, so memory stays at one chunk (30 s is about 1 MB of WAV) however
long the meeting runs.
"""
from __future__ import annotations

import array
import io
import os
import shutil
import subprocess
import wave
from pathlib import Path

_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin")

#: Peak level (of 32767) below which a chunk counts as silence and is not
#: transcribed: Whisper invents text ("Thank you.") for silent audio.
SILENCE_PEAK = 300

MISSING = ("ffmpeg is not installed, so meeting audio cannot be converted for transcription. "
           "Install it with `brew install ffmpeg` (macOS) or your package manager, then restart OpenProgram.")


class AudioError(RuntimeError):
    """A chunk could not be converted; the message says why."""


def ffmpeg() -> str | None:
    """Path of ``ffmpeg``, or ``None``."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    for folder in _DIRS:
        candidate = Path(folder) / "ffmpeg"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _speech_ready(data: bytes) -> bool:
    """Whether ``data`` is already 16 kHz mono 16-bit PCM WAV."""
    try:
        with wave.open(io.BytesIO(data)) as reader:
            return (reader.getframerate(), reader.getnchannels(), reader.getsampwidth(),
                    reader.getcomptype()) == (16000, 1, 2, "NONE")
    except (wave.Error, EOFError):
        return False


def to_wav(source: Path) -> bytes:
    """``source`` (any format ffmpeg reads) as 16 kHz mono 16-bit WAV."""
    if source.suffix.lower() == ".wav":
        data = source.read_bytes()
        if _speech_ready(data):
            return data
    tool = ffmpeg()
    if not tool:
        raise AudioError(MISSING)
    argv = [tool, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(source),
            "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", "-f", "wav", "pipe:1"]
    try:
        done = subprocess.run(argv, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise AudioError("ffmpeg took longer than 2 minutes to convert a chunk.") from exc
    if done.returncode != 0 or not done.stdout:
        tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
        raise AudioError(f"ffmpeg could not convert the chunk: {tail[0]}")
    return done.stdout


def wav_info(data: bytes) -> tuple[int, int]:
    """``(duration_ms, peak)`` of 16-bit WAV data; ``(0, 0)`` when it is not WAV."""
    try:
        with wave.open(io.BytesIO(data)) as reader:
            frames = reader.readframes(reader.getnframes())
            rate = reader.getframerate() or 16000
            width = reader.getsampwidth()
            channels = reader.getnchannels() or 1
    except (wave.Error, EOFError):
        return 0, 0
    duration = int(len(frames) / max(1, width * channels) * 1000 / rate)
    if width != 2 or not frames:
        return duration, 32767 if frames else 0
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) // 2 * 2])
    peak = max((abs(v) for v in samples), default=0)
    return duration, peak


__all__ = ["AudioError", "MISSING", "SILENCE_PEAK", "ffmpeg", "to_wav", "wav_info"]
