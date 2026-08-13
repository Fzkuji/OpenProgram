"""The ``read_conversation`` tool implementation."""
from __future__ import annotations

from openprogram.programs._runtime import function

_DESC = (
    "Read an agent's conversation (a session branch) as a plain-text transcript: every turn's "
    "user / assistant content plus the tool and function calls that turn "
    "made (name, arguments, result, success or failure). Use it to learn "
    "what was actually done in earlier work — distilling a session into a "
    "reusable skill, reviewing how a task was solved, or recovering a "
    "procedure from a conversation that is no longer in context. "
    "Defaults to the current session's active branch. Find session ids "
    "and branch tips with `list_agents`. Read a turn range with "
    "start_turn/end_turn (1-based, inclusive; negatives count from the "
    "end — start_turn=-10 reads the last 10 turns, handy for the "
    "conclusion of a long session)."
)


@function(
    name="read_conversation",
    description=_DESC,
    toolset=["core"],
    max_result_chars=80_000,
)
def read_conversation(
    session_id: str = "",
    head_id: str = "",
    start_turn: int = 0,
    end_turn: int = 0,
    include_function_calls: bool = True,
    max_chars: int = 60000,
) -> str:
    """Render a session branch as readable text.

    Args:
        session_id: Session to read. Empty means the current session.
        head_id: Branch tip to walk back from (the HEAD half of a
            `list_agents` `SID:HEAD` target). Empty means the
            session's active branch.
        start_turn: First turn to include, as the 1-based `[N]` number
            shown in the transcript (inclusive). 0 means from the first
            turn; negative counts from the end — `start_turn=-10` reads
            the last 10 turns, handy for a session's conclusion.
        end_turn: Last turn to include (inclusive). 0 means through the
            last turn; `end_turn=-1` is the last turn.
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
            "[read_conversation error] no session_id given and no current "
            "session — pass a session_id (see list_agents)."
        )
    # A `SID:HEAD` target pasted whole from list_agents: split it
    # rather than failing on a session id that does not exist.
    head = (head_id or "").strip()
    if ":" in sid and not head:
        sid, _, head = sid.partition(":")

    from openprogram.store.session.transcript import render_session_transcript

    try:
        return render_session_transcript(
            sid,
            head_id=head or None,
            start_turn=int(start_turn),
            end_turn=int(end_turn),
            include_function_calls=bool(include_function_calls),
            max_chars=max(1_000, int(max_chars)),
        )
    except Exception as e:  # noqa: BLE001 — tool results report, never raise
        return f"[read_conversation error] {type(e).__name__}: {e}"


def _current_session() -> str | None:
    try:
        from openprogram.agent.run_control import _current_session_id
        return _current_session_id.get(None)
    except Exception:
        return None
