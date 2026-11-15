"""Voice input routes — speech to text for the composer's microphone.

Endpoints:
  GET  /api/voice/status      engine choice, what ``auto`` resolves to, and
                              which engines are usable on this computer
  POST /api/voice/transcribe  body = one recorded phrase as WAV → {text}

The engines live in :mod:`openprogram.interfaces.speech.stt`.
"""
from __future__ import annotations

import asyncio

from fastapi import Request
from fastapi.responses import JSONResponse


def register(app):
    @app.get("/api/voice/status")
    async def api_voice_status():
        from openprogram.interfaces.speech import stt as voice
        return JSONResponse(await asyncio.to_thread(voice.status))

    @app.post("/api/voice/transcribe")
    async def api_voice_transcribe(request: Request):
        from openprogram.interfaces.speech import stt as voice
        audio = await request.body()
        if len(audio) > voice.MAX_AUDIO_BYTES:
            return JSONResponse({"error": "The recording is too long to transcribe in one piece."},
                                status_code=413)
        try:
            result = await asyncio.to_thread(voice.transcribe, audio)
        except voice.VoiceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(result)
