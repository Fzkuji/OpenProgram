"""The link between the core and the speech interface's speech to text.

Core code (the computer-use tool transcribing a recording it made, the
meeting recorder transcribing call audio with timestamps) turns audio into text without importing the speech interface. The speech package
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


__all__ = [
    "SpeechLink", "SpeechUnavailable", "TranscriptionError",
    "register", "unregister", "current", "transcribe", "transcribe_segments",
]
