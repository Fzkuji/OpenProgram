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

    def transcribe_file(self, path: str, language: str = "") -> dict:
        from pathlib import Path
        from openprogram.interfaces.speech import audio, stt
        try:
            wav = audio.to_wav(Path(path))
        except audio.AudioError as exc:
            raise speech_link.TranscriptionError(str(exc)) from exc
        duration, peak = audio.wav_info(wav)
        if peak < audio.SILENCE_PEAK:
            # Whisper invents text ("Thank you.") for silent audio.
            return {"duration_ms": duration, "silent": True, "segments": [], "engine": "", "model": ""}
        result = stt.transcribe_segments(wav, language or "auto")
        return {"duration_ms": duration, "silent": False, **result}

    def diarize(self, chunks: list[tuple[int, str]]) -> list[tuple[int, int, int]]:
        from pathlib import Path
        from openprogram.interfaces.speech import audio, diarize

        def converted():
            for t_ms, path in chunks:
                try:
                    yield int(t_ms), audio.to_wav(Path(path))
                except (audio.AudioError, OSError):
                    continue  # a chunk that does not convert was not transcribed either
        try:
            return diarize.run(converted())
        except diarize.Unavailable as exc:
            raise speech_link.DiarizationUnavailable(exc.reason, str(exc)) from exc

    def readiness(self) -> dict:
        from openprogram.interfaces.speech import audio, stt
        try:
            speech = stt.resolve_engine() is not None
        except Exception:
            speech = False
        return {"speech": speech, "ffmpeg": audio.ffmpeg() is not None, "ffmpeg_missing": audio.MISSING}


speech_link.register(SpeechToText())
