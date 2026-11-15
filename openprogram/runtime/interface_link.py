"""The link between the core and the interface that shows live sessions.

Core code tells the open clients that something changed — a goal, a todo
list, a title, a branch, a native window, a streamed execution frame, the
context ring — without knowing which interface shows them. It also reads the
interface's in-memory copy of an open session, moves HEAD through it, and
finds the documentation pages the interface ships. The Server
registers its implementation with :func:`register` when it loads; every core
call goes through the functions below.

With no interface registered (a CLI process, a test without the Server) the
notices go nowhere and :func:`live_session` reports no in-memory session,
which is what the Server does while no client is connected.

Frames sent here go straight to the clients. :func:`openprogram.runtime.events.emit_ws_frame`
is the bus route for producers that also want the frame in the event log;
these notices are high-volume or carry captures, so they skip the log.
"""
from __future__ import annotations

from typing import Any, Protocol


class InterfaceLink(Protocol):
    """The operations an interface implements for the core."""

    def broadcast(self, frame: dict) -> None:
        """Send one ``{"type", "data"}`` frame to every connected client."""

    def broadcast_chat_response(self, session_id: str, msg_id: str, response: dict) -> None:
        """Send one ``chat_response`` envelope of a session to every client."""

    def refresh_context_stats(self, session_id: str, msg_id: str = "") -> None:
        """Re-estimate and send the context ring after the graph moved."""

    def branches_changed(self, session_id: str) -> None:
        """Send the session's current branch list to every client."""

    def live_session(self, session_id: str) -> dict | None:
        """The interface's in-memory state of an open session, if any."""

    def session_busy(self, session_id: str) -> bool:
        """Whether the interface is running a turn of the session right now."""

    def set_active_head(self, session_id: str, head_id: str) -> bool:
        """Move the session HEAD and the interface's in-memory copy together."""

    def session_on_screen(self, session_id: str) -> bool:
        """Whether a connected client shows the session in a window that is not hidden."""

    def document_file(self, page: str) -> str:
        """The installed file of one product documentation page, or ``""``."""


_link: InterfaceLink | Any | None = None


def register(link: InterfaceLink | Any) -> None:
    """Make ``link`` the interface link of this process (the last one wins)."""
    global _link
    _link = link


def unregister(link: InterfaceLink | Any | None = None) -> None:
    """Drop the registered link, or only ``link`` when it is the current one."""
    global _link
    if link is None or _link is link:
        _link = None


def current() -> InterfaceLink | Any | None:
    return _link


def broadcast(frame: dict) -> None:
    """Send one frame to every connected client."""
    if _link is not None:
        _link.broadcast(frame)


def broadcast_chat_response(session_id: str, msg_id: str, response: dict) -> None:
    """Send one ``chat_response`` envelope; the interface may drop it after a stop."""
    if _link is not None:
        _link.broadcast_chat_response(session_id, msg_id, response)


def refresh_context_stats(session_id: str, msg_id: str = "") -> None:
    if _link is not None:
        _link.refresh_context_stats(session_id, msg_id)


def branches_changed(session_id: str) -> None:
    if _link is not None:
        _link.branches_changed(session_id)


def live_session(session_id: str) -> dict | None:
    return _link.live_session(session_id) if _link is not None else None


def session_busy(session_id: str) -> bool:
    """Whether an interface is running a turn of the session (not in the store yet)."""
    return bool(_link.session_busy(session_id)) if _link is not None else False


def session_on_screen(session_id: str) -> bool:
    """Whether a client shows the session now; a finished turn there is not unread."""
    return bool(_link.session_on_screen(session_id)) if _link is not None else False


def set_active_head(session_id: str, head_id: str) -> bool:
    """Move HEAD through the interface, which keeps its own copy of the branch.

    With no interface there is no in-memory copy and the caller's store write
    already moved HEAD, so this reports False and changes nothing.
    """
    return bool(_link.set_active_head(session_id, head_id)) if _link is not None else False


def document_file(page: str) -> str:
    """The installed file of a product documentation page (``capabilities/workflows/authoring``)."""
    return _link.document_file(page) if _link is not None else ""
