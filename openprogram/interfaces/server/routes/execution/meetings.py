"""Meeting routes — record a call's audio, transcribe it and summarize it.

Endpoints:
  GET    /api/meetings                         every meeting, the one recording, ffmpeg and speech readiness
  POST   /api/meetings                         {title?, language?} → the new meeting's status; starts the
                                               server's recording of the computer's sound. ``language``
                                               is an ISO code; empty (the default) detects it per chunk,
                                               whatever the voice-input language is
  GET    /api/meetings/{id}                    status: state, elapsed time, chunk and transcript progress,
                                               system_audio ("recording" | "unavailable") and its reason
  POST   /api/meetings/{id}/audio?track=&seq=&t_ms=   body = one audio chunk of a track (WebM/Opus, Ogg, MP4, WAV)
  POST   /api/meetings/{id}/stop               stop recording; transcription and the summary go on
  GET    /api/meetings/{id}/transcript         the merged transcript lines
  GET    /api/meetings/{id}/summary            summary.md
  POST   /api/meetings/{id}/summarize          write the summary again
  DELETE /api/meetings/{id}                    delete the meeting and its audio

The page records the microphone and posts it here; the server records
the computer's sound output itself (ScreenCaptureKit on macOS) and feeds
it to the same chunk intake, then converts and transcribes both. Meetings
are the owner's: every route needs the owner's sign-in. The recorder is
the built-in application ``meeting``, reached through the service it
provides; while the application is disabled every route answers 404.
"""
from __future__ import annotations

import asyncio

from fastapi import Request
from fastapi.responses import JSONResponse

NOT_ENABLED = {"error": "Meetings is not enabled.", "reason_code": "meeting_not_enabled"}
_NO_STORE = {"Cache-Control": "no-store"}
_SUFFIXES = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4", "audio/wav": "wav", "audio/x-wav": "wav"}


def _recorder():
    from openprogram.programs._host import services as _services
    return _services.lookup("meeting")


def _owner(request: Request) -> JSONResponse | None:
    from openprogram.operations.self_update.control.projection import ProjectionAccessError
    from ..self_updates import require_owner
    try:
        require_owner(request)
    except ProjectionAccessError:
        return JSONResponse({"error": "Meetings are only available to the owner."}, status_code=403)
    return None


def _speech_ready() -> bool:
    try:
        from openprogram.interfaces.speech import stt
        return stt.resolve_engine() is not None
    except Exception:
        return False


def register(app):
    def missing() -> JSONResponse:
        return JSONResponse({"error": "Unknown meeting."}, status_code=404)

    def guard(request: Request):
        recorder = _recorder()
        if recorder is None:
            return None, JSONResponse(NOT_ENABLED, status_code=404)
        refused = _owner(request)
        return (None, refused) if refused is not None else (recorder, None)

    def directory(recorder, meeting_id: str):
        try:
            return recorder.directory_of(meeting_id)
        except recorder.MeetingError:
            return None

    @app.get("/api/meetings")
    async def api_meetings(request: Request):
        recorder, refused = guard(request)
        if refused is not None:
            return refused
        meetings = await asyncio.to_thread(recorder.list_meetings)
        live = recorder.active()
        return JSONResponse({
            "meetings": meetings,
            "active": live.id if live is not None else None,
            "ffmpeg": recorder.ffmpeg_ready(),
            "ffmpeg_missing": recorder.FFMPEG_MISSING,
            "speech": await asyncio.to_thread(_speech_ready),
            "max_seconds": recorder.MAX_SECONDS,
        }, headers=_NO_STORE)

    @app.post("/api/meetings")
    async def api_meeting_start(request: Request):
        recorder, refused = guard(request)
        if refused is not None:
            return refused
        if not recorder.ffmpeg_ready():
            return JSONResponse({"error": recorder.FFMPEG_MISSING, "reason_code": "ffmpeg_missing"},
                                status_code=409)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        title = str(body.get("title") or "")
        language = str(body.get("language") or "")
        try:
            meeting = await asyncio.to_thread(recorder.start, title, language)
        except recorder.MeetingError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(meeting.status(), headers=_NO_STORE)

    @app.get("/api/meetings/{meeting_id}")
    async def api_meeting_status(meeting_id: str, request: Request):
        recorder, refused = guard(request)
        if refused is not None:
            return refused
        found = directory(recorder, meeting_id)
        if found is None:
            return missing()
        return JSONResponse(recorder.read_meta(found), headers=_NO_STORE)

    @app.post("/api/meetings/{meeting_id}/audio")
    async def api_meeting_audio(meeting_id: str, request: Request):
        recorder, refused = guard(request)
        if refused is not None:
            return refused
        live = recorder.get(meeting_id)
        if live is None:
            return missing()
        params = request.query_params
        try:
            seq = int(params.get("seq", ""))
            t_ms = int(float(params.get("t_ms", "")))
        except ValueError:
            return JSONResponse({"error": "seq and t_ms must be numbers."}, status_code=400)
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        data = await request.body()
        try:
            status = await asyncio.to_thread(live.append, params.get("track", ""), seq, t_ms, data,
                                             _SUFFIXES.get(content_type, "webm"))
        except recorder.MeetingError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(status, headers=_NO_STORE)

    @app.post("/api/meetings/{meeting_id}/stop")
    async def api_meeting_stop(meeting_id: str, request: Request):
        recorder, refused = guard(request)
        if refused is not None:
            return refused
        live = recorder.get(meeting_id)
        if live is not None:
            await asyncio.to_thread(live.finish, "done")
        found = directory(recorder, meeting_id)
        if found is None:
            return missing()
        return JSONResponse(recorder.read_meta(found), headers=_NO_STORE)

    @app.get("/api/meetings/{meeting_id}/transcript")
    async def api_meeting_transcript(meeting_id: str, request: Request):
        recorder, refused = guard(request)
        if refused is not None:
            return refused
        found = directory(recorder, meeting_id)
        if found is None:
            return missing()
        return JSONResponse({"id": meeting_id, "lines": recorder.transcript(found)}, headers=_NO_STORE)

    @app.get("/api/meetings/{meeting_id}/summary")
    async def api_meeting_summary(meeting_id: str, request: Request):
        recorder, refused = guard(request)
        if refused is not None:
            return refused
        found = directory(recorder, meeting_id)
        if found is None or not (found / "summary.md").is_file():
            return missing()
        return JSONResponse({"id": meeting_id, "markdown": (found / "summary.md").read_text(encoding="utf-8")},
                            headers=_NO_STORE)

    @app.post("/api/meetings/{meeting_id}/summarize")
    async def api_meeting_summarize(meeting_id: str, request: Request):
        recorder, refused = guard(request)
        if refused is not None:
            return refused
        if directory(recorder, meeting_id) is None:
            return missing()
        try:
            meeting = await asyncio.to_thread(recorder.resummarize, meeting_id)
        except recorder.MeetingError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(meeting.status(), headers=_NO_STORE)

    @app.delete("/api/meetings/{meeting_id}")
    async def api_meeting_delete(meeting_id: str, request: Request):
        recorder, refused = guard(request)
        if refused is not None:
            return refused
        if directory(recorder, meeting_id) is None:
            return missing()
        await asyncio.to_thread(recorder.delete, meeting_id)
        return JSONResponse({"ok": True})
