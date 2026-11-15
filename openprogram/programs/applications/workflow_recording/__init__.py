"""Workflow recording, as an application.

The user demonstrates a task on their own computer: every click, key and
scroll is attributed to the application, window and Accessibility
element it landed on, with screenshots and spoken narration. The finished
recording goes into the next chat message, where the Agent turns it into
a workflow (purpose ``workflow``) or, as a secondary use, reads it only
as context for the request in that message (purpose ``context``).

- ``session``: one recording at a time, its files, time limit and life cycle.
- ``describe``: a finished recording as message text and image blocks.
- ``macos``: the capture backend (event tap, Accessibility, screenshots).
- ``service``: the ``workflow_recording`` service the core reaches all of
  this through.

It has no Agent entries. ``setup`` provides the ``workflow_recording``
service; the server's recording routes and the chat message composition
look it up, and the composer shows its record item only while the
application is enabled. Disposing the context withdraws the service and
stops a recording in progress.
"""

#: What the Launchpad, Applications page and Abilities call this application
#: (``openprogram.programs._host.identity`` reads them from the source).
TITLE = "Workflow Recording"
DESCRIPTION = "Record a task you demonstrate on this computer so the Agent can turn it into a workflow."
ICON = "recording"


def setup(ctx) -> None:
    from . import session
    from .service import SERVICE, WorkflowRecordingService

    ctx.provide(SERVICE, WorkflowRecordingService())

    def stop_live() -> None:
        live = session.active()
        if live is not None:
            live.finish("disabled")

    ctx.effect(stop_live, "stop live recording")
