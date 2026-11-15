"""Tell the agent when a command it left running in the background ends.

As in Claude Code, a background command that exits on its own starts a turn
in its conversation saying so, unless a ``bash`` or ``write_stdin`` reply has
already shown the agent that exit. A conversation that is mid-turn gets the
notice once that turn ends. The notice turn reuses the conversation's last
chat request, so it has exactly the permissions the user last gave it.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from types import SimpleNamespace

from openprogram.host.processes.store import ProcessStore
from openprogram.runtime.permissions.identity import detached_from_window, inherited_automation

POLL_SECONDS = 2.0
_SOURCE = "process_exit"
_OUTPUT_TAIL = 2000
_log = logging.getLogger(__name__)


def notice_text(record: dict, output: str) -> str:
    seconds = max(0, int((record.get("ended_at") or time.time()) - record["started_at"]))
    status = (f"exited with code {record['exit_code']}" if record["status"] == "exited"
              else "failed in its supervisor")
    tail = output[-_OUTPUT_TAIL:]
    lines = [
        "A command you left running in the background has ended.",
        f"session_id: {record['id']}",
        f"command: {record['command']}",
        f"status: {status} after {seconds} s",
        "Last output:" if tail.strip() else "It printed nothing.",
    ]
    if tail.strip():
        lines.append(tail if len(output) <= _OUTPUT_TAIL else "…" + tail)
    lines.append("Act on this if it matters for the user's request; otherwise reply briefly.")
    return "\n".join(lines)


def _latest_chat(executions, store):
    for execution in executions:  # newest first
        payload = store.get_agent_turn_input(execution.execution_id)
        if payload and payload.get("kind") == "chat":
            return execution, payload
    return None, None


def deliver(record: dict, processes: ProcessStore) -> bool:
    """Admit the notice turn; False while the conversation is busy or cannot take one."""
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.conversation.run_control import is_turn_running, is_worker_stopping
    from openprogram.runtime.events import emit_ws_frame
    from openprogram.runtime.execution import default_store

    session_id = record["session_id"]
    store = default_store()
    if is_worker_stopping() or is_turn_running(session_id) or store.list_nonterminal(session_id=session_id):
        return False
    execution, payload = _latest_chat(store.list_for_session(session_id), store)
    source = store.get_execution_input(execution.execution_id) if execution else None
    if source is None:
        # Not a chat conversation (a sub-agent's branch, a script): nobody to tell.
        processes.update(record["id"], reported=True)
        return True
    key = f"{_SOURCE}:{record['id']}"
    message_id = store.admission_execution_id(session_id, key) + "_user"
    request = dict(payload["request"])
    request.update(automation=inherited_automation(SimpleNamespace(**request)),
                   detached=detached_from_window(SimpleNamespace(**request)),
                   user_text=notice_text(record, processes.output(record["id"])),
                   source=_SOURCE, user_msg_id=message_id, user_already_persisted=False,
                   history_override=None, attachments=None, spawn_caller=None,
                   goal_context=None, goal_trigger=False, goal_verification=None,
                   goal_previous_execution=None, surface_context=None)
    request.pop("branch_from", None)
    adapter = CanonicalAgentAdapter(store=store, event_sink=emit_ws_frame)
    admission = adapter.admit_payload(
        session_id=session_id, payload=dict(payload, request=request),
        trusted_actor=source.trusted_actor, user_message_id=message_id,
        assistant_message_id=f"{message_id}_reply",
        config_snapshot_ref=source.config_snapshot_ref, admission_key=key,
    )
    processes.update(record["id"], reported=True)

    def activate():
        try:
            asyncio.run(adapter.activate(admission))
        except Exception:
            adapter.fail_admission(admission, reason_code="process_exit_activation_failed")

    threading.Thread(target=activate, daemon=True, name="process-exit-notice").start()
    return True


def sweep(store: ProcessStore | None = None) -> None:
    """Deliver due notices, then drop records past retention."""
    store = store or ProcessStore()
    for record in store.unreported_exits():
        try:
            deliver(record, store)
        except Exception:
            _log.warning("process exit notice for %s deferred", record["id"], exc_info=True)
    store.prune()


async def run_exit_notices(stop: asyncio.Event) -> None:
    """Worker loop: deliver due notices until ``stop`` is set."""
    while not stop.is_set():
        await asyncio.to_thread(sweep)
        try:
            await asyncio.wait_for(stop.wait(), POLL_SECONDS)
        except asyncio.TimeoutError:
            pass
