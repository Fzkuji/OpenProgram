"""The link between the core and the speech interface's speech to text.

Core code (the computer-use tool transcribing a recording it made, an
application transcribing and diarizing what it recorded through its
``speech`` capability) turns audio into text without importing the speech
interface. The speech package
(``openprogram.interfaces.speech``) registers its implementation with
:func:`register` when it is imported; the worker, the Server and the CLI's
in-process turns import it at start.

A failed transcription raises :class:`TranscriptionError` with a message the
caller shows as is. With nothing registered :func:`transcribe` raises
:class:`SpeechUnavailable`, which is a ``TranscriptionError``, so a caller
reports "not transcribed" and names the reason.
"""
from __future__ import annotations

from typing import Any, Protocol


class TranscriptionError(RuntimeError):
    """Audio could not be turned into text; the message says why."""


class SpeechUnavailable(TranscriptionError):
    """A call needs speech to text and no speech interface is registered."""


class SpeechLink(Protocol):
    """The operations the speech interface implements for the core."""

    def transcribe(self, audio: bytes) -> dict[str, str]:
        """``{"text", "engine", "model"}`` of one recording; raises :class:`TranscriptionError`."""

    def transcribe_segments(self, audio: bytes, language: str = "") -> dict[str, Any]:
        """``{"segments": [{"start_ms", "end_ms", "text"}], "engine", "model"}`` of one
        WAV recording, with times from the start of the audio. ``language`` is
        an ISO code, or ``""`` to detect it (never the voice-input setting).
        Optional: a link without it is reached through :func:`transcribe_segments`'s
        fallback."""

    def transcribe_file(self, path: str, language: str = "") -> dict[str, Any]:
        """One recording file in any format ffmpeg reads: ``{"duration_ms", "silent",
        "segments", "engine", "model"}``. A silent recording is not transcribed."""

    def diarize(self, chunks: list[tuple[int, str]]) -> list[tuple[int, int, int]]:
        """Speaker turns ``(start_ms, end_ms, speaker)`` of one track given as its
        ``(t_ms, path)`` chunks in order; raises :class:`DiarizationUnavailable`."""

    def readiness(self) -> dict[str, Any]:
        """``{"speech": bool, "ffmpeg": bool, "ffmpeg_missing": str}``."""


class DiarizationUnavailable(RuntimeError):
    """Speakers cannot be told apart here; ``reason`` is ``not_installed`` or ``no_model``."""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason


_link: SpeechLink | Any | None = None


def register(link: SpeechLink | Any) -> None:
    """Make ``link`` the speech link of this process (the last one wins)."""
    global _link
    _link = link


def unregister(link: SpeechLink | Any | None = None) -> None:
    """Drop the registered link, or only ``link`` when it is the current one."""
    global _link
    if link is None or _link is link:
        _link = None


def current() -> SpeechLink | Any | None:
    return _link


def transcribe(audio: bytes) -> dict[str, str]:
    if _link is None:
        raise SpeechUnavailable(
            "no speech interface is registered; import openprogram.interfaces.speech "
            "(the worker, the Server and CLI turns do) before transcribing."
        )
    return _link.transcribe(audio)


def transcribe_segments(audio: bytes, duration_ms: int = 0, language: str = "") -> dict[str, Any]:
    """Timed segments of one WAV recording (meeting audio), in ``language``
    (an ISO code) or, when it is ``""``, the language detected from the audio.

    A link without ``transcribe_segments`` answers with its plain text as
    one segment spanning ``duration_ms``.
    """
    if _link is None:
        transcribe(audio)  # raises SpeechUnavailable
    timed = getattr(_link, "transcribe_segments", None)
    if callable(timed):
        return timed(audio, language=language)
    result = _link.transcribe(audio)
    text = str(result.get("text") or "").strip()
    segments = [{"start_ms": 0, "end_ms": int(duration_ms), "text": text}] if text else []
    return {"segments": segments, "engine": result.get("engine", ""), "model": result.get("model", "")}


def _require() -> SpeechLink | Any:
    if _link is None:
        transcribe(b"")  # raises SpeechUnavailable
    return _link


def transcribe_file(path: str, language: str = "") -> dict[str, Any]:
    """A recording file's timed segments; see :meth:`SpeechLink.transcribe_file`."""
    return _require().transcribe_file(path, language=language)


def diarize(chunks: list[tuple[int, str]]) -> list[tuple[int, int, int]]:
    """Speaker turns of one track; see :meth:`SpeechLink.diarize`."""
    return _require().diarize(chunks)


def readiness() -> dict[str, Any]:
    """Whether speech to text and ffmpeg are there; everything false without a link."""
    if _link is None:
        return {"speech": False, "ffmpeg": False, "ffmpeg_missing": ""}
    return _link.readiness()


__all__ = [
    "DiarizationUnavailable", "SpeechLink", "SpeechUnavailable", "TranscriptionError",
    "register", "unregister", "current", "transcribe", "transcribe_segments",
    "transcribe_file", "diarize", "readiness",
]
