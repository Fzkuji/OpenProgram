"""Meetings, as an application.

While the user is in a call (Zoom, Teams, a browser meeting, anything that
plays through this computer), two tracks are recorded: the microphone (the
user, "Me"), which the Meetings page records and uploads, and the
computer's sound output (everyone else, "Others"), which the server
records itself with ScreenCaptureKit on macOS. Both arrive in 30-second
chunks; each is converted with ffmpeg when needed, transcribed with timestamps by the speech engine chosen in
Settings → Voice, and merged into one time-ordered transcript. When the
recording stops, a new conversation summarizes the meeting (summary,
decisions, action items, open questions) and ``summary.md`` keeps the
summary with the full transcript.

- ``session``: one meeting recording at a time, its files and life cycle.
- ``system_audio``: the computer's sound, recorded on the server and cut into chunks.
- ``audio``: ffmpeg conversion and the silence check.
- ``transcript``: the two tracks merged into one transcript.
- ``summary``: the summary turn and ``summary.md``.
- ``diarize``: telling apart the other speakers when a meeting stops (optional sherpa-onnx).
- ``service``: the ``meeting`` service the server's routes reach.

It has no Agent entries. ``setup`` provides the ``meeting`` service; the
``/api/meetings`` routes look it up and answer 404 without it. Disposing
the context withdraws the service and stops a recording in progress.
Recording never starts on its own: only the Start button in the Meetings
window starts it.
"""

#: What the Launchpad, Applications page and Abilities call this application
#: (``openprogram.programs._host.identity`` reads them from the source).
TITLE = "Meetings"
DESCRIPTION = "Record what everyone says in a call on this computer, transcribe it and summarize the meeting."
ICON = "meeting"


def setup(ctx) -> None:
    from . import session
    from .service import SERVICE, MeetingService

    ctx.provide(SERVICE, MeetingService())
    ctx.effect(lambda: session.stop_all("disabled"), "stop live meeting recording")
