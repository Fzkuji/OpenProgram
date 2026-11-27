"""Continue an interrupted chat as a new turn after a worker restart.

The control plane records a restart intent and decides eligibility
(``openprogram.runtime.execution.recovery.chat_recovery``); admitting and activating
the new turn needs the Agent engine, so this module is the
``recovery_turn_starter`` hook that ``execution.recovery.restart`` calls.
"""
from __future__ import annotations

from contextlib import nullcontext
from typing import Any


class _NothingCarriedOn:
    """The restart continuation of a process where no Program carries a turn on."""

    def locked(self, session_id: str):
        return nullcontext()

    def branch(self, session_id: str, request: dict) -> str | None:
        return request.get("branch_id")

    def check(self, store: Any, execution: Any, new_execution_id: str, request: dict) -> str | None:
        return None


def continuation_guard() -> Any:
    """What a Program that carries turns on (a chat Goal) says about a restart
    continuation (the ``restart_continuation`` turn moment)."""
    from openprogram.runtime.conversation import turn_hooks

    return turn_hooks.provider("restart_continuation") or _NothingCarriedOn()


def start_recovery_turn(store, execution, event):
    """Consume one saved restart intent through ordinary immutable admission."""
    import asyncio
    import threading
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.events import emit_ws_frame
    from openprogram.runtime.execution.recovery import restart
    from openprogram.runtime.execution.recovery.chat_recovery import recovery_state
    from openprogram.runtime.execution.projections.foreground import active_foreground_task
    from openprogram.runtime.execution.model import ExecutionStatus

    data = event.payload
    source = store.get_execution_input(execution.execution_id)
    payload = store.get_agent_turn_input(execution.execution_id)
    if source is None or not payload or payload.get("kind") != "chat":
        return "unsupported_input"
    request = dict(payload["request"])
    if (request.get("interaction") not in {None, "interactive"}
            or request.get("source") not in {"web", "tui", "acp"}):
        return "noninteractive"
    from openprogram.runtime.execution.projections.execution_tree import continuation_key

    key = continuation_key(execution.execution_id, event.sequence)
    new_id = store.admission_execution_id(execution.session_id, key)
    continuation = continuation_guard()
    with continuation.locked(execution.session_id):
        current = store.get_execution(execution.execution_id)
        closed = (current.status is ExecutionStatus.INTERRUPTED
                  and current.reason_code == "restart_new_turn"
                  and current.status_version == data["expected_version"] + 1)
        if current.status_version != data["expected_version"] and not closed:
            return "execution_changed"
        if (restart.window_seconds() == 0 or restart.time() < data["interrupted_at"]
                or (data["resume_before"] is not None and restart.time() > data["resume_before"])):
            return "expired"
        state = recovery_state(store, current)
        if not state["can_start_new_turn"]:
            return None
        # The turn's own branch: the continuation is superseded only by a
        # foreground turn on the branch it continues, and a turn running on
        # another branch of the conversation does not hold it back.
        branch_id = continuation.branch(execution.session_id, request)
        foreground = active_foreground_task(store, execution.session_id, selected_branch=branch_id)
        if foreground and foreground["execution_id"] != new_id:
            return "superseded"
        # What the interrupted turn was carrying on (a chat Goal) decides
        # whether the new turn may continue it, and binds it to the turn.
        refusal = continuation.check(store, execution, new_id, request)
        if refusal is not None:
            return refusal
        from openprogram.runtime.permissions.identity import HANDOFF_TURNS
        if request.get("automation") in HANDOFF_TURNS:
            # A chain knows the first turn's message; a second restart keeps it.
            request["resumes_message_id"] = request.get("resumes_message_id") or request.get("user_msg_id")
        request.update(
            # The last sentence names the prompt's source, as Codex wraps its
            # recovery input in an internal-context tag: the chat hides this
            # turn's prompt, and the model should not read it as the user.
            user_text="Continue the interrupted task using saved conversation history. Inspect actual state before acting. Prior tool results may be unknown; do not assume failure or repeat an external action without confirmation. This note comes from the runtime after a worker restart, not from the user.",
            user_msg_id=new_id + "_user", user_already_persisted=False,
            history_override=None, attachments=None, surface_context=None, spawn_caller=None,
            goal_verification=None, restart_resume=True,
        )
        request.pop("branch_from", None)
        adapter = CanonicalAgentAdapter(store=store, event_sink=emit_ws_frame)
        admission = adapter.admit_payload(
            session_id=execution.session_id, payload=dict(payload, request=request),
            trusted_actor=source.trusted_actor, user_message_id=request["user_msg_id"],
            assistant_message_id=request["user_msg_id"] + "_reply",
            config_snapshot_ref=source.config_snapshot_ref, admission_key=key,
            recovery_from=(current.execution_id, current.status_version),
        )

    def activate():
        latest = store.get_execution(admission.execution_id)
        if latest.status is not ExecutionStatus.QUEUED or latest.current_attempt_id:
            return  # Startup's checkpoint consumer owns already recovered admissions.
        try:
            ran = asyncio.run(adapter.activate(admission))
        except Exception:
            # A concurrent activation/CAS must never fail the winning owner.
            latest = store.get_execution(admission.execution_id)
            if latest.status is ExecutionStatus.QUEUED and latest.current_attempt_id is None:
                adapter.fail_admission(admission, reason_code="restart_activation_failed")
                restart.not_continued(store, latest)
            return
    threading.Thread(target=activate, name="chat-restart", daemon=True).start()
    return "new_turn_admitted"
