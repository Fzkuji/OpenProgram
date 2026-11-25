"""The Meetings application's operations, as ``application.json`` declares them.

Each runs in a process of its own with ``context`` as its only way to the
host: ``context.files`` and ``load``/``save`` for storage,
``transcribe``/``diarize``/``speech_readiness`` for speech, and
``open_conversation``/``converse`` for the summary. The window calls
``readiness``, ``start``, ``transcript``, ``summary``, ``summarize`` and
``delete``; the host calls ``ingest`` for every recorded chunk and
``finish`` once a recording has stopped and every chunk was taken.
"""
from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from . import meetings as _meetings
from . import speakers as _speakers
from . import summary as _summary


def readiness(value, context):
    """Whether recordings can be converted (ffmpeg) and transcribed (a speech engine)."""
    return context.speech_readiness()


def start(value, context):
    """A new meeting, ``recording``; the window then has the host record it under its id."""
    title = " ".join(str(value.get("title") or "").split())[:160]
    language = _meetings.language_of(value.get("language"))
    started = time.time()
    meeting_id = uuid.uuid4().hex[:12]
    meta = _meetings.new_meta(meeting_id, title or _meetings.default_title(started), language, started)
    _meetings.folder(context, meeting_id).mkdir(parents=True, exist_ok=True)

    def add(document):
        document["meetings"][meeting_id] = meta
    _meetings.update(context, add)
    return meta


def ingest(value, context):
    """One recorded chunk: transcribe it and keep its timed segments beside it."""
    meeting_id = _meetings.checked_id(value["key"])
    track, seq, t_ms = value["track"], int(value["seq"]), int(value["t_ms"])
    relative = str(value["path"])
    path = context.files / relative
    meta = _meetings.meta(context, meeting_id)
    record = {"track": track, "seq": seq, "t_ms": t_ms, "segments": []}
    try:
        heard = context.transcribe(relative, language=meta.get("language") or "")
        record["duration_ms"] = int(heard.get("duration_ms") or 0)
        if heard.get("silent"):
            record["silent"] = True
        else:
            record["segments"] = list(heard.get("segments") or [])
    except Exception as exc:  # one bad chunk must not stop the meeting
        heard = {}
        record["error"] = str(exc) or type(exc).__name__
    _meetings.write_json(path.with_suffix(".json"), record)
    count = len(_meetings.lines(_meetings.folder(context, meeting_id)))

    def apply(document):
        current = document["meetings"].get(meeting_id)
        if not isinstance(current, dict):
            return
        chunks = dict(current.get("chunks") or {})
        chunks[track] = int(chunks.get(track) or 0) + 1
        current["chunks"] = chunks
        if record.get("error"):
            current["failed"] = int(current.get("failed") or 0) + 1
            if record["error"] not in (current.get("errors") or []):
                current["errors"] = ((current.get("errors") or []) + [record["error"]])[-5:]
        elif record.get("silent"):
            current["skipped"] = int(current.get("skipped") or 0) + 1
        else:
            current["transcribed"] = int(current.get("transcribed") or 0) + 1
        current["engine"] = str(heard.get("engine") or current.get("engine") or "")
        current["model"] = str(heard.get("model") or current.get("model") or "")
        current["lines"] = count
    _meetings.update(context, apply)
    return {"seq": seq, "track": track, "segments": len(record["segments"])}


def _separate_speakers(context, meeting_id: str, directory: Path) -> None:
    """Diarize the whole system track once and label its lines "Speaker 1", "Speaker 2"…"""
    found = _meetings.records(directory)
    keys = sorted(key for key in found if key[0] == "system")
    chunk_records = [found[key] for key in keys]
    if not any(record.get("segments") for record in chunk_records):
        return
    _meetings.change_meta(context, meeting_id, diarization="running", diarization_reason="", diarization_message="")
    chunks = []
    for (_track, seq), record in zip(keys, chunk_records):
        audio = next((p for p in sorted((directory / "system").glob(f"{seq:05d}.*")) if p.suffix != ".json"), None)
        if audio is not None:
            chunks.append({"t_ms": int(record.get("t_ms") or 0),
                           "path": audio.relative_to(context.files).as_posix()})
    try:
        answer = context.diarize(chunks)
    except Exception as exc:  # the transcript keeps "Others"
        _meetings.change_meta(context, meeting_id, diarization="failed", diarization_reason="error",
                              diarization_message=str(exc) or type(exc).__name__)
        return
    if not answer.get("available"):
        _meetings.change_meta(context, meeting_id, diarization="unavailable",
                              diarization_reason=str(answer.get("reason") or ""),
                              diarization_message=str(answer.get("message") or ""))
        return
    turns = [(int(t["start_ms"]), int(t["end_ms"]), int(t["speaker"])) for t in answer.get("turns") or []]
    labelled = _speakers.assign(chunk_records, turns)
    for (_track, seq), record in zip(keys, labelled):
        _meetings.write_json(directory / "system" / f"{seq:05d}.json", record)
    count = len({segment["speaker"] for record in labelled
                 for segment in record.get("segments") or [] if segment.get("speaker")})
    _meetings.change_meta(context, meeting_id, diarization="done", speakers=count)


def _write_summary(context, meeting_id: str) -> dict:
    """Write the summary in a new conversation and keep it as ``summary.md``."""
    directory = _meetings.folder(context, meeting_id)
    found = _meetings.lines(directory)
    meta = _meetings.meta(context, meeting_id)

    def complete(answer: str, error: str) -> dict:
        error = error if not answer.strip() else ""
        current = _meetings.meta(context, meeting_id)
        (directory / "summary.md").write_text(_summary.render(current, answer, found, error), encoding="utf-8")
        return _meetings.change_meta(context, meeting_id, state="done", summary_error=error, has_summary=True)

    if not found:
        errors = meta.get("errors") or []
        return complete("", errors[-1] if errors else "Nothing was transcribed.")
    _meetings.change_meta(context, meeting_id, state="summarizing", summary_error="")
    try:
        session_id = context.open_conversation(meta.get("title") or "Meeting")["session_id"]
    except Exception as exc:
        return complete("", f"The summary conversation could not start: {exc}")
    meta = _meetings.change_meta(context, meeting_id, session_id=session_id)
    try:
        reply = context.converse(session_id, _summary.compose(meta, found))
    except Exception as exc:
        return complete("", str(exc) or type(exc).__name__)
    return complete(str(reply.get("answer") or ""), str(reply.get("error") or ""))


def finish(value, context):
    """The recording stopped and every chunk was taken: speakers, transcript, summary."""
    meeting_id = _meetings.checked_id(value["key"])
    recording = value.get("recording") or {}
    directory = _meetings.folder(context, meeting_id)
    _meetings.change_meta(
        context, meeting_id, state="transcribing",
        duration_ms=int(recording.get("duration_ms") or 0), ended_at=time.time(),
        end_reason=str(recording.get("end_reason") or ""),
        system_audio=str(recording.get("system_audio") or ""),
        system_audio_reason=str(recording.get("system_audio_reason") or ""),
        system_audio_message=str(recording.get("system_audio_message") or ""),
    )
    _separate_speakers(context, meeting_id, directory)
    found = _meetings.lines(directory)
    _meetings.write_json(directory / "transcript.json", {"id": meeting_id, "lines": found})
    _meetings.change_meta(context, meeting_id, lines=len(found))
    return _write_summary(context, meeting_id)


def transcript(value, context):
    """The merged transcript lines of one meeting, as far as it is transcribed."""
    meeting_id = _meetings.checked_id(value["id"])
    _meetings.meta(context, meeting_id)
    return {"id": meeting_id, "lines": _meetings.lines(_meetings.folder(context, meeting_id))}


def summary(value, context):
    """``summary.md`` of one meeting, ``""`` before it is written."""
    meeting_id = _meetings.checked_id(value["id"])
    path = _meetings.folder(context, meeting_id) / "summary.md"
    return {"id": meeting_id, "markdown": path.read_text(encoding="utf-8") if path.is_file() else ""}


def summarize(value, context):
    """Write the summary again (after a failure or an interruption)."""
    meeting_id = _meetings.checked_id(value["id"])
    _meetings.meta(context, meeting_id)
    return _write_summary(context, meeting_id)


def delete(value, context):
    """Delete a meeting's audio, transcript and summary; its conversation stays."""
    meeting_id = _meetings.checked_id(value["id"])
    shutil.rmtree(_meetings.folder(context, meeting_id), ignore_errors=True)

    def drop(document):
        document["meetings"].pop(meeting_id, None)
    _meetings.update(context, drop)
    return {"ok": True}


operations = {
    "readiness": readiness, "start": start, "ingest": ingest, "finish": finish,
    "transcript": transcript, "summary": summary, "summarize": summarize, "delete": delete,
}

__all__ = ["operations"] + list(operations)
