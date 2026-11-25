"""An approval asked while a live process keeps running.

The tool-call gate suspends the execution at a safe point and resumes it
with the answer. An outside agent (Codex, Claude Code, an ACP agent) is a
running process that waits for its own answer, so its approvals are live
waits instead: opened on the running attempt, shown as the same
``question.asked`` card (the web and channel relays draw it like any
other), and answered through ``execution.wait.answer`` while the turn
blocks here.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

_log = logging.getLogger(__name__)

#: How often the waiting turn reads the wait's state.
_POLL_SECONDS = 0.2


@dataclass(frozen=True)
class LiveOwner:
    """The running attempt an approval belongs to."""

    session_id: str
    execution_id: str
    attempt_id: str
    generation: int
    producer_id: str
    #: The execution store the attempt lives in (the default one when None).
    store: Any = None


@dataclass(frozen=True)
class LiveDecision:
    """``allow`` (with the scope picked), ``deny``, or ``cancelled`` (Stop or timeout)."""

    outcome: str
    scope: str = "once"

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"


def ask_live_approval(
    owner: LiveOwner,
    *,
    tool: str,
    arguments: Mapping[str, Any],
    prompt: str,
    detail: str = "",
    allowed_scopes: Sequence[str] = ("once", "session"),
    risk_level: str = "medium",
    publish: Callable[[dict], None],
    cancel_event: Optional[threading.Event] = None,
    timeout: Optional[float] = None,
) -> LiveDecision:
    """Open an approval wait, show it, and block until it is settled.

    ``publish`` sends the ``question.asked`` frame (the turn's event sink);
    the bus also gets the event, so a channel bound to the conversation
    relays it. A Stop (``cancel_event``) closes the wait and returns
    ``cancelled``.
    """
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.waits import DurableWaitStore, WaitStatus, question_frame_data

    store = owner.store if owner.store is not None else default_store()
    waits = DurableWaitStore(store)
    scopes = [scope for scope in allowed_scopes if scope in ("once", "session", "always")] or ["once"]
    wait = waits.open_wait(
        execution_id=owner.execution_id, attempt_id=owner.attempt_id, generation=owner.generation,
        kind="approval",
        request={
            "prompt": prompt, "options": ["允许", "拒绝"], "multi": False, "allow_custom": False,
            "detail": detail, "schema": {}, "questions": [],
            "tool": tool, "args": dict(arguments), "risk_level": risk_level,
            "allowed_scopes": scopes,
        },
        policy_snapshot={
            "version": 1, "kind": "approval", "mode": "live", "producer_id": owner.producer_id,
            "on_answer": "continue", "on_decline": "continue", "on_timeout": "fail",
            "allowed_scopes": scopes,
        },
        expires_at=0 if timeout is None else time.time() + timeout,
    )
    execution = store.get_execution(owner.execution_id)
    data = question_frame_data(wait, session_id=owner.session_id,
                               expected_version=execution.status_version if execution else 0)
    try:
        publish({"type": "question.asked", "data": data})
        from openprogram.runtime.events import emit_safe
        emit_safe("question.asked", "agent", data, {"session": owner.session_id})
    except Exception:  # noqa: BLE001 - the wait stays answerable from the recovery list
        _log.warning("live approval %s was not announced", wait.wait_id, exc_info=True)

    def close(reason: str) -> LiveDecision:
        waits.cancel_live_waits(
            execution_id=owner.execution_id, attempt_id=owner.attempt_id,
            generation=owner.generation, producer_id=owner.producer_id, wait_id=wait.wait_id,
        )
        try:
            publish({"type": "question.rejected", "data": {"id": wait.wait_id, "reason": reason}})
        except Exception:  # noqa: BLE001
            _log.debug("live approval %s retraction not sent", wait.wait_id, exc_info=True)
        return LiveDecision("cancelled")

    while True:
        if cancel_event is not None and cancel_event.is_set():
            return close("cancelled")
        current = waits.get_wait(wait.wait_id)
        if current is None:
            return LiveDecision("cancelled")
        if current.status is WaitStatus.RESOLVED:
            answer = current.answer if isinstance(current.answer, Mapping) else {}
            return LiveDecision("allow", str(answer.get("scope") or "once"))
        if current.status is WaitStatus.DECLINED:
            return LiveDecision("deny")
        if current.status in (WaitStatus.EXPIRED, WaitStatus.CANCELLED):
            return LiveDecision("cancelled")
        if 0 < current.expires_at <= time.time():
            return close("timeout")
        if cancel_event is not None:
            cancel_event.wait(_POLL_SECONDS)
        else:
            time.sleep(_POLL_SECONDS)


def live_owner_from(execution_context: Optional[Mapping[str, Any]], session_id: str,
                    producer: str) -> Optional[LiveOwner]:
    """The live owner of the attempt a turn runs in, or None outside an execution."""
    hook = (execution_context or {}).get("safe_point_hook")
    attempt = getattr(hook, "attempt", None)
    if attempt is None:
        return None
    return LiveOwner(
        session_id=session_id, execution_id=attempt.execution_id, attempt_id=attempt.attempt_id,
        generation=int(attempt.generation), producer_id=f"{producer}:{attempt.attempt_id}",
        store=getattr(getattr(hook, "driver", None), "executions", None),
    )
