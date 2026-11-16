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


async def handle_get_running_sessions(ws, cmd: dict):
    """Report every conversation that has a turn running, on any branch.

    A client that connects mid-turn hears no ``running_task`` for turns
    already going, so it asks once per connection. The answer holds one
    run state per running conversation, each as ``get_run_state`` gives
    it; a conversation the client still shows as running and that is not
    listed has ended meanwhile.
    """
    import asyncio
    from openprogram.interfaces.server import server as _s
    session_ids = await asyncio.to_thread(running_session_ids, _s)
    states = []
    for session_id in session_ids:
        try:
            state = await run_for_conversation(session_id, run_state_data, _s, session_id)
        except Exception:
            # A conversation whose lock is held answers at its own load.
            continue
        if state["run_active"] or state["running_branches"]:
            states.append(state)
    await ws.send_text(json.dumps({"type": "running_sessions", "data": {"sessions": states}}))


def running_session_ids(_s) -> list[str]:
    """Conversations with a held run slot or an unfinished foreground turn."""
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.projections.foreground import foreground_task_snapshot

    with _s._running_tasks_lock:
        found = set(_s._running_tasks) | {key[0] for key in _s._branch_running_tasks}
    try:
        store = default_store()
        for execution in store.list_nonterminal():
            if (execution.session_id not in found and not execution.parent_execution_id
                    and foreground_task_snapshot(store, execution) is not None):
                found.add(execution.session_id)
    except Exception:
        import logging
        logging.getLogger(__name__).debug("running executions not listed", exc_info=True)
    return sorted(found)


async def handle_follow_up_answer(ws, cmd: dict):
    """User answered a follow-up question from a running function."""
    from openprogram.interfaces.server import server as _s
    fq_session_id = cmd.get("session_id", "")
    answer = cmd.get("answer", "")
    with _s._follow_up_lock:
        fq = _s._follow_up_queues.get(fq_session_id)
    if fq is not None:
        fq.put(answer)
