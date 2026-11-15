"""The ``meeting`` service: everything of the meeting recorder the server reaches.

The application's ``setup`` provides one :class:`MeetingService` as the
service ``meeting``. The core never imports the application; the routes
look the service up (``openprogram.programs._host.services.lookup("meeting")``)
and treat ``None`` as "Meetings is not enabled".
"""
from __future__ import annotations

from pathlib import Path

from . import audio, session

#: The name the application provides the service under.
SERVICE = "meeting"


class MeetingService:
    """The meeting recorder, as the server reaches it."""

    MAX_SECONDS = session.MAX_SECONDS
    TRACKS = session.TRACKS
    MeetingError = session.MeetingError
    FFMPEG_MISSING = audio.MISSING

    @staticmethod
    def system_audio_support() -> dict:
        """Whether this computer's sound can be recorded for the "Others" track, and why not."""
        from . import system_audio
        return system_audio.support()

    @staticmethod
    def start(title: str = "", language: str = "") -> session.Meeting:
        return session.start(title, language)

    @staticmethod
    def get(meeting_id: str) -> session.Meeting | None:
        return session.get(meeting_id)

    @staticmethod
    def active() -> session.Meeting | None:
        return session.active()

    @staticmethod
    def directory_of(meeting_id: str) -> Path:
        return session.directory_of(meeting_id)

    @staticmethod
    def read_meta(directory: Path) -> dict:
        return session.read_meta(directory)

    @staticmethod
    def list_meetings() -> list[dict]:
        return session.list_meetings()

    @staticmethod
    def transcript(directory: Path) -> list[dict]:
        return session.transcript_lines(directory)

    @staticmethod
    def resummarize(meeting_id: str) -> session.Meeting:
        return session.resummarize(meeting_id)

    @staticmethod
    def delete(meeting_id: str) -> None:
        session.delete(meeting_id)

    @staticmethod
    def ffmpeg_ready() -> bool:
        return audio.ffmpeg() is not None
