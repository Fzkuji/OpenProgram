"""Start an owner chat turn in a conversation without a client sending it.

Reminders that wake the agent and messages handed over from another
conversation need a turn the owner sees in the conversation, under that conversation's own permission settings, as if the
owner had typed ``text``. The turn runs on a daemon thread; ``on_done``
receives its result (``None`` when it never ran).
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable

# A wake waits this long for a running turn in its conversation.
WAKE_WAIT_S = 600.0
WAKE_POLL_S = 2.0


def start_owner_turn_when_free(session_id: str, agent_id: str, text: str, *,
                               wait_s: float = WAKE_WAIT_S, poll_s: float = WAKE_POLL_S,
                               give_up_when: Callable[[], bool] | None = None,
                               **options: Any) -> str | None:
    """Wait for the conversation's current turn, then start ``text`` as the next one.

    The wake takes the run slot itself, so it never runs beside another
    turn. Blocks the calling thread; returns the execution id, or ``None``
    when the conversation stayed busy for ``wait_s``, or as soon as
    ``give_up_when`` says so, before anything is admitted. A conversation
    whose lock could not be had (``SessionLockTimeout``: a long load or a
    move held it) counts as busy: the wake tries again until the wait ends.
    """
    from openprogram.runtime.conversation.run_control import is_turn_running
    from openprogram.runtime.conversation.run_control.run_slot import RunSlotBusy
    from openprogram.state.store.session.session_lock import SessionLockTimeout

    deadline = time.monotonic() + wait_s
    while True:
        if give_up_when is not None and give_up_when():
            return None
        if not is_turn_running(session_id):
            try:
                return start_owner_turn(session_id, agent_id, text, **options)
            except (RunSlotBusy, SessionLockTimeout):
                pass
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_s)


def start_owner_turn(
    session_id: str, agent_id: str, text: str, *,
    tools: list[str] | None = None,
    name: str = "owner-turn",
    reason_code: str = "owner_turn_activation_failed",
    on_done: Callable[[Any], None] | None = None,
    msg_id: str | None = None,
    quiet_finish: bool = False,
    automation: str | None = None,
    source: str = "web",
    authority: dict[str, Any] | None = None,
    unattended: bool = False,
    on_event: Callable[[dict], None] | None = None,
) -> str:
    """Admit the turn now and run it in the background; return its execution id.

    ``msg_id`` names the turn's user message, so a caller can recognise the
    turn from inside it (``TurnRequest.user_msg_id``); a new id by default.

    ``quiet_finish``: the turn's end does not mark the conversation unread
    (``TurnRequest.quiet_finish``); a failure still does. ``automation`` says
    why a turn nobody typed started (``TurnRequest.automation``).

    ``source`` names the surface the text came from, ``authority`` the
    authority it is admitted under (the local owner's by default) and
    ``unattended`` adds the run note for a turn no one watches
    (``turn_input.build_turn_request``). ``on_event`` receives every frame
    the turn sends, after the frame has gone to the App.

    The turn takes the conversation's run slot like a typed message does and
    holds it until it ends. Raises :class:`RunSlotBusy` when another turn
    holds it, and ``SessionLockTimeout`` when the conversation's lock could
    not be had to take it; nothing is admitted then.
    """
    import asyncio
    import threading
    from openprogram.runtime.permissions.authority import local_owner_authority
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.conversation.turn_input import build_turn_request
    from openprogram.runtime.events import emit_ws_frame
    from openprogram.runtime.conversation.run_control import run_slot
    msg_id = msg_id or uuid.uuid4().hex
    if not run_slot.reserve(session_id, msg_id):
        raise run_slot.RunSlotBusy(session_id)
    try:
        authority = dict(authority) if authority is not None else local_owner_authority()
        request = build_turn_request(
            session_id, text, agent_id=agent_id, source=source, msg_id=msg_id,
            authority=authority, unattended=unattended,
            run_config=("thinking_effort", "additional_working_dirs"),
            tools_override=tools, quiet_finish=quiet_finish, automation=automation,
        )

        def sink(frame: dict) -> None:
            emit_ws_frame(frame)
            if on_event is not None:
                on_event(frame)

        adapter = CanonicalAgentAdapter(event_sink=sink)
        admission = adapter.admit(request, trusted_actor=authority, user_message_id=msg_id,
                                  assistant_message_id=f"{msg_id}_reply",
                                  config_snapshot_ref=f"session:{session_id}")
    except BaseException:
        run_slot.release(session_id, msg_id)
        raise

    def activate():
        result = None
        try:
            activated = asyncio.run(adapter.activate(admission))
            # ``None``: a stop reached the turn before it ran; no result.
            result = activated[1] if activated is not None else None
        except Exception:
            adapter.fail_admission(admission, reason_code=reason_code)
        finally:
            run_slot.finish(session_id, msg_id, execution_id=admission.execution_id)
        if on_done is not None:
            on_done(result)
    try:
        thread = threading.Thread(target=activate, name=name, daemon=True)
        if not run_slot.activate(session_id, msg_id, execution_id=admission.execution_id,
                                 status_version=getattr(admission, "status_version", 0),
                                 runner=thread):
            raise run_slot.RunSlotBusy(session_id)
        thread.start()
    except BaseException:
        adapter.fail_admission(admission, reason_code=reason_code)
        run_slot.finish(session_id, msg_id, execution_id=admission.execution_id)
        run_slot.release(session_id, msg_id)
        raise
    return admission.execution_id


__all__ = ["WAKE_WAIT_S", "start_owner_turn", "start_owner_turn_when_free"]
