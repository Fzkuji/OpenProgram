"""Run-state probe and follow-up answers for a running function."""
from __future__ import annotations

import json

from openprogram.state.store.session.conversation_work import run_for_conversation


async def handle_get_run_state(ws, cmd: dict):
    """Report run state without changing this socket's focused session."""
    from openprogram.interfaces.server import server as _s
    session_id = cmd.get("session_id")
    # The durable run records are read under the conversation's lock: off
    # the event loop, so a held conversation delays only this answer.
    data = await run_for_conversation(session_id, run_state_data, _s, session_id)
    await ws.send_text(json.dumps({"type": "run_state", "data": data}))


def run_state_data(_s, session_id) -> dict:
    run_seq = _s._next_run_seq()
    # ``run_active`` is the selected branch's turn, as it has always been;
    # ``running_branches`` lists every running turn with its branch.
    run_active = bool(session_id and _s._is_selected_run_active(session_id))
    return {
        "session_id": session_id,
        "run_active": run_active,
        "run_task": _s._run_identity(session_id) if run_active else None,
        "running_branches": _s._running_branches(session_id) if session_id else [],
        "run_seq": run_seq,
    }


async def handle_follow_up_answer(ws, cmd: dict):
    """User answered a follow-up question from a running function."""
    from openprogram.interfaces.server import server as _s
    fq_session_id = cmd.get("session_id", "")
    answer = cmd.get("answer", "")
    with _s._follow_up_lock:
        fq = _s._follow_up_queues.get(fq_session_id)
    if fq is not None:
        fq.put(answer)
