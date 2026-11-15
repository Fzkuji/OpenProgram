"""The speech interface's implementation of the core's speech link.

``openprogram.runtime.speech_link`` is how core code turns audio into text
without importing this interface. This module implements it with
:mod:`openprogram.interfaces.speech.stt` and registers it when
``openprogram.interfaces.speech`` is imported.

The operation looks ``stt.transcribe`` up when it runs, so the engines load
on first use and a test that patches ``stt.transcribe`` is seen.
"""
from __future__ import annotations

from openprogram.runtime import speech_link


class SpeechToText:
    """Speech to text with the engine chosen in Settings → Voice."""

    def transcribe(self, audio: bytes) -> dict[str, str]:
        from openprogram.interfaces.speech import stt
        return stt.transcribe(audio)

    def transcribe_segments(self, audio: bytes, language: str = "") -> dict:
        from openprogram.interfaces.speech import stt
        return stt.transcribe_segments(audio, language or "auto")


speech_link.register(SpeechToText())
