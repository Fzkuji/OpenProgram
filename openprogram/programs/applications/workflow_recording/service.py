"""The ``workflow_recording`` service: everything of the recorder the core reaches.

The application's ``setup`` provides one :class:`WorkflowRecordingService`
as the service ``workflow_recording``. The core never imports the
application; it looks the service up
(``openprogram.programs._host.services.lookup("workflow_recording")``) and
treats ``None`` as "recording is not enabled": the recording routes answer
404 and a chat message naming a recording is refused.

Members:

- ``TAG``, ``MAX_SECONDS``, ``PURPOSES``, ``RecordingError``: the message
  tag, the time limit, the purposes (``workflow``, ``context``) and the
  error a missing or refused recording raises.
- ``start(purpose)``, ``get(id)``, ``active()``, ``directory_of(id)``:
  the recording life cycle (``session``).
- ``summary(directory)``, ``compose(directory)``, ``step_rows(directory)``:
  a finished recording as the composer card, the message text with its
  image blocks, and the card's step list (``describe``).
"""
from __future__ import annotations

from pathlib import Path

from . import describe, session

#: The name the application provides the service under.
SERVICE = "workflow_recording"


class WorkflowRecordingService:
    """The demonstration recorder, as the core reaches it."""

    TAG = describe.TAG
    MAX_SECONDS = session.MAX_SECONDS
    PURPOSES = session.PURPOSES
    RecordingError = session.RecordingError

    @staticmethod
    def support() -> dict:
        """Whether this computer can record a demonstration, and why not."""
        return session.support()

    @staticmethod
    def start(purpose: str = "workflow") -> session.Recording:
        return session.start(purpose)

    @staticmethod
    def get(recording_id: str) -> session.Recording | None:
        return session.get(recording_id)

    @staticmethod
    def active() -> session.Recording | None:
        return session.active()

    @staticmethod
    def directory_of(recording_id: str) -> Path:
        return session.directory_of(recording_id)

    @staticmethod
    def summary(directory: Path) -> dict:
        return describe.summary(directory)

    @staticmethod
    def compose(directory: Path) -> tuple[str, list[dict]]:
        return describe.compose(directory)

    @staticmethod
    def step_rows(directory: Path) -> list[dict]:
        """Each event as the card's row: time, type, step text and screenshot name."""
        return [{"t": int(event.get("t") or 0), "type": event.get("type"), "text": describe.step_text(event),
                 "image": (event.get("image") or "").removeprefix("images/") or None}
                for event in describe.read_events(directory)]


def get() -> WorkflowRecordingService | None:
    """The provided service, or ``None`` while workflow recording is not enabled."""
    from openprogram.programs._host import services as _services
    return _services.lookup(SERVICE)


#: What a caller reports when the application is not loaded.
NOT_ENABLED = "workflow recording is not enabled"
