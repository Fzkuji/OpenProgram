"""Meetings, kept in the application's own storage.

The host records a meeting (``audio.record``): the microphone as "Me"
and the computer's sound as "Others", in chunks of about 30 seconds on one
clock, written under the application's files at
``recordings/<id>/<track>/<seq>.<ext>``. Each chunk reaches ``ingest``,
which transcribes it (``speech``) and writes its timed segments beside it
as ``<seq>.json``; when the recording stops, ``finish`` tells apart the
speakers of the computer's sound, writes ``transcript.json`` and has the
summary written in a new conversation (``conversation``), saved as
``summary.md`` in the same folder.

The meeting list is the application's state document (``storage.app``):
``{"meetings": {<id>: <meta>}, "prefs": {...}}``. Each meta carries the
title, times, state, counts, the conversation and what the host reported
about the computer's sound and the speakers.

States: ``recording`` → ``transcribing`` → ``summarizing`` → ``done``.
A meeting whose recording ended without ``finish`` (the worker stopped)
shows as interrupted in the window; its chunks are kept and ``summarize``
finishes it.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import transcript as _transcript

TRACKS = ("mic", "system")
MAX_SECONDS = 4 * 3600
LIVE_STATES = ("recording", "transcribing", "summarizing")
_ID = re.compile(r"[0-9a-f]{12}\Z")


class MeetingError(ValueError):
    """A meeting cannot start or does not exist; the window shows the message."""


def language_of(value: Any) -> str:
    """A meeting's spoken language: an ISO 639 code, or ``""`` to detect it per chunk."""
    language = str(value or "").strip().lower()
    if language in ("", "auto"):
        return ""
    if not re.fullmatch(r"[a-z]{2,3}", language):
        raise MeetingError(f"Unknown language: {language[:16]}")
    return language


def checked_id(value: Any) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise MeetingError("Unknown meeting.")
    return value


def folder(context, meeting_id: str) -> Path:
    """Where one meeting's audio, segments, transcript and summary are."""
    return context.files / "recordings" / checked_id(meeting_id)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows refuses the rename while a reader (the window's transcript read) has the
    # target open; that lasts milliseconds, so try again for up to two seconds.
    for attempt in range(20):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == 19:
                temp.unlink(missing_ok=True)
                raise
            time.sleep(0.1)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------------- the state document
def document(context) -> dict:
    value = context.load()["value"]
    return value if isinstance(value, dict) else {}


def update(context, change: Callable[[dict], Any]) -> Any:
    """Apply ``change`` to the state document and save it, again if someone saved first."""
    for _ in range(50):
        loaded = context.load()
        value = loaded["value"] if isinstance(loaded["value"], dict) else {}
        value.setdefault("meetings", {})
        result = change(value)
        try:
            context.save(value, expected_version=loaded["version"])
        except ValueError as exc:
            if "changed" in str(exc):
                time.sleep(0.02)
                continue
            raise
        return result
    raise MeetingError("The meeting list kept changing; try again.")


def meta(context, meeting_id: str) -> dict:
    found = (document(context).get("meetings") or {}).get(checked_id(meeting_id))
    if not isinstance(found, dict):
        raise MeetingError("Unknown meeting.")
    return found


def change_meta(context, meeting_id: str, **fields: Any) -> dict:
    """Merge ``fields`` into one meeting's meta; returns the new meta."""
    def apply(value: dict) -> dict:
        current = value["meetings"].get(meeting_id)
        if not isinstance(current, dict):
            raise MeetingError("Unknown meeting.")
        current.update(fields)
        return dict(current)
    return update(context, apply)


def new_meta(meeting_id: str, title: str, language: str, started: float) -> dict:
    return {
        "id": meeting_id, "title": title, "language": language,
        "started_at": started, "ended_at": None, "duration_ms": 0,
        "state": "recording", "end_reason": "",
        "chunks": {track: 0 for track in TRACKS},
        "transcribed": 0, "skipped": 0, "failed": 0, "errors": [], "lines": 0,
        "engine": "", "model": "", "session_id": "", "summary_error": "", "has_summary": False,
        "max_seconds": MAX_SECONDS,
        "system_audio": "", "system_audio_reason": "", "system_audio_message": "",
        "diarization": "", "diarization_reason": "", "diarization_message": "", "speakers": 0,
    }


def default_title(started: float) -> str:
    return time.strftime("Meeting %Y-%m-%d %H:%M", time.localtime(started))


# ---------------------------------------------------------------------- segments
def records(directory: Path) -> dict[tuple[str, int], dict]:
    """Every transcribed chunk of a meeting, by ``(track, seq)``."""
    found: dict[tuple[str, int], dict] = {}
    for track in TRACKS:
        for path in sorted((directory / track).glob("*.json")):
            record = read_json(path)
            if isinstance(record, dict):
                found[(track, int(record.get("seq") or 0))] = record
    return found


def lines(directory: Path) -> list[dict]:
    """The merged transcript of what is transcribed so far."""
    chunks = records(directory)
    return _transcript.merge(chunks[key] for key in sorted(chunks))


__all__ = ["LIVE_STATES", "MAX_SECONDS", "MeetingError", "TRACKS", "change_meta", "checked_id", "default_title",
           "document", "folder", "language_of", "lines", "meta", "new_meta", "read_json", "records", "update",
           "write_json"]
