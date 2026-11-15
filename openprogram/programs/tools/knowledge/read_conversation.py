"""The ``read_conversation`` tool implementation."""
from __future__ import annotations

from openprogram.programs._host.runtime import function

_DESC = (
    "Read an agent's conversation (a session branch) as a plain-text transcript: every turn's "
    "user / assistant content plus the tool and function calls that turn "
    "made (name, arguments, result, success or failure). Use it to learn "
    "what was actually done in earlier work — reviewing how a task was "
    "solved, or recovering a "
    "procedure from a conversation that is no longer in context. "
    "Defaults to the current session's active branch. Find session ids "
    "and branch tips with `list_agents`. Read a turn range with "
    "start_turn/end_turn (1-based, inclusive; negatives count from the "
    "end — start_turn=-10 reads the last 10 turns, handy for the "
    "conclusion of a long session). session_id may also be an execution_id "
    "from the agent tool or send_message: that reads the agent's result, or "
    "its status while it still runs."
)


@function(
    name="read_conversation",
    description=_DESC,
    toolset=["core"],
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
        session_id: Session to read, or an execution_id to read that
            agent's result. Empty means the current session.
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
    if not head:
        result = _execution_result(sid)
        if result is not None:
            return result

    from openprogram.state.store.session.transcript import render_session_transcript, session_read_violation

    from openprogram.state.memory.policy import consume_sources
    try:
        from openprogram.runtime.context.session_db import default_db
        store = default_db()
        violation = session_read_violation(store, sid)
        if violation:
            return "[read_conversation error] " + violation
        return render_session_transcript(
            sid,
            head_id=head or None,
            start_turn=int(start_turn),
            end_turn=int(end_turn),
            include_function_calls=bool(include_function_calls),
            max_chars=max(1_000, int(max_chars)),
            on_consumed=consume_sources,
            store=store,
        )
    except Exception as e:  # noqa: BLE001 — tool results report, never raise
        return f"[read_conversation error] {type(e).__name__}: {e}"


def _execution_result(ident: str) -> str | None:
    """An execution id's result, as job_output gave it (which this replaced
    for the model); None when ``ident`` is not an execution."""
    try:
        from openprogram.runtime.context.session_db import default_db
        if default_db().get_session(ident) is not None:
            return None
        from openprogram.runtime.jobs import get_runner
        if get_runner().get_job(ident) is None:
            return None
        from openprogram.programs.tools.agents.agent.job_output.job_output import _job_output_impl
        out = _job_output_impl(ident, block=False)
    except Exception:
        return None
    if isinstance(out, str):
        return out
    return "\n".join(getattr(part, "text", "") for part in out.content)


def _current_session() -> str | None:
    try:
        from openprogram.runtime.execution.run_control.binding import (
            _current_session_id,
        )
        return _current_session_id.get(None)
    except Exception:
        return None
