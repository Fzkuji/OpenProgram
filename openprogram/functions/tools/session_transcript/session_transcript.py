"""The ``session_transcript`` tool implementation."""
from __future__ import annotations

from openprogram.functions._runtime import function

_DESC = (
    "Read a past session as a plain-text transcript: every turn's "
    "user / assistant content plus the tool and function calls that turn "
    "made (name, arguments, result, success or failure). Use it to learn "
    "what was actually done in earlier work — distilling a session into a "
    "reusable skill, reviewing how a task was solved, or recovering a "
    "procedure from a conversation that is no longer in context. "
    "Defaults to the current session's active branch. Find session ids "
    "with `list_sessions` and branch tips with `list_branches`."
)


@function(
    name="session_transcript",
    description=_DESC,
    toolset=["core"],
    max_result_chars=80_000,
)
def session_transcript(
    session_id: str = "",
    head_id: str = "",
    include_function_calls: bool = True,
    max_chars: int = 60000,
) -> str:
    """Render a session branch as readable text.

    Args:
        session_id: Session to read. Empty means the current session.
        head_id: Branch tip to walk back from (the HEAD half of a
            `list_branches` `SID:HEAD` target). Empty means the
            session's active branch.
        include_function_calls: Include the tool / function calls each
            turn made. Set false for a conversation-only view.
        max_chars: Size budget for the transcript. Later turns are
            dropped once it is reached.
    """
    sid = (session_id or "").strip()
    if not sid:
        sid = _current_session() or ""
    if not sid:
        return (
            "[session_transcript error] no session_id given and no current "
            "session — pass a session_id (see list_sessions)."
        )
    # A `SID:HEAD` target pasted whole from list_branches: split it
    # rather than failing on a session id that does not exist.
    head = (head_id or "").strip()
    if ":" in sid and not head:
        sid, _, head = sid.partition(":")

    from openprogram.store.session.transcript import render_session_transcript

    try:
        return render_session_transcript(
            sid,
            head_id=head or None,
            include_function_calls=bool(include_function_calls),
            max_chars=max(1_000, int(max_chars)),
        )
    except Exception as e:  # noqa: BLE001 — tool results report, never raise
        return f"[session_transcript error] {type(e).__name__}: {e}"


def _current_session() -> str | None:
    try:
        from openprogram.webui._pause_stop import _current_session_id
        return _current_session_id.get(None)
    except Exception:
        return None
