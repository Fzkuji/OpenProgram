"""The moments of a conversation turn that Programs react to.

The conversation runtime owns a turn from its admission to the settlement
of its execution, and Programs sit above it: a chat Goal governs which
turns it admits and settles when one ends, the task board follows a reply,
``send_file`` attaches files to it, the screen tool releases what a run
kept. The runtime never imports them. It names the moment and calls the
handlers registered for it; each Program registers its own when its package
is imported (``openprogram.programs`` imports every tool package when it
loads), so the runtime names no Program and a Program changes without
touching it.

Each entry of :data:`MOMENTS` says when the runtime calls the handlers, with
what, and what it does with the answer; the call site keeps the order and
the exception behaviour stated there. A moment with no handler is the turn
of a process where no Program reacts: nothing is wrapped, settled or
attached, and a *single* moment (one provider answers) falls back to the
runtime's own behaviour.

Handlers are looked up when a moment happens, never cached, and the lookup
first loads the Programs through ``host.runtime_facts.load_programs`` (a
dictionary lookup once they are in): a Program package loads on first use,
which can be later than the turn that needs it, and this keeps registration
independent of when that was. A handler resolves the functions it calls at
call time, so a test that replaces one of them is what the runtime reaches.
"""
from __future__ import annotations

from collections.abc import Mapping
from threading import RLock
from typing import Any

from openprogram.host import runtime_facts

MOMENTS: dict[str, str] = {
    # ── admission and the turn ─────────────────────────────────────────
    "turn_admission": (
        "single: (entry, **admission) -> admission. Admits a chat turn in "
        "place of ``entry._admit_without_goal(**admission)``, which it calls "
        "for any turn it does not govern."
    ),
    "turn_scope": (
        "(request, execution_id) -> context manager or None. Entered, in "
        "registration order, around an activated chat turn; exits in reverse."
    ),
    "turn_events": (
        "(on_event, request) -> on_event. Wraps the event callback of a turn "
        "before it runs, in registration order."
    ),
    "verification_turn": (
        "single: an object with require_candidate(session_id, request, *, "
        "execution_id), wrap_read(tool, request) and instructions(session_id, "
        "request), for a turn that verifies a completion candidate "
        "(``request.goal_verification``). Such a turn cannot run without it."
    ),
    "turn_prompt": (
        "(request) -> (part name, text) or None. A part appended to the "
        "system prompt of a turn that does not own its conversation (a "
        "background check-in)."
    ),
    "turn_notes": (
        "(request, view) -> [(note id, text, fields)]. Notes sent beside the "
        "message of the conversation's own turn, decided from what its "
        "rendered history shows (``view``, None when the engine did not say)."
    ),
    # ── the reply ──────────────────────────────────────────────────────
    "reply_task_owner": (
        "(request) -> {'task', 'step'} or None. The task a reply continues "
        "from its first block; the first answer that is not None wins."
    ),
    "reply_segment": (
        "(request, assistant_msg_id, task_id) -> None. The first real call of "
        "a reply that continues a task begins the reply's segment of it."
    ),
    "reply_attachments": (
        "(final_text, tool_calls, request, started_at) -> (text, paths). "
        "Markers of the files a turn hands back, added to the reply text, "
        "and the paths a channel may upload; chained in registration order."
    ),
    "reply_ended": (
        "(request, assistant_msg_id) -> None. A reply that ended normally "
        "(not stopped); a failing handler leaves the reply whole."
    ),
    # ── the end of a run ───────────────────────────────────────────────
    "turn_finished": (
        "(executions, execution) -> None. The driver finished a turn whose "
        "execution is terminal; called in order, an exception propagates."
    ),
    "execution_terminal": (
        "(executions, execution) -> None. The control plane settles an "
        "execution that reached a terminal status; a handler defers what it "
        "cannot settle now itself, any other exception propagates after the "
        "run's resources are released."
    ),
    "execution_released": (
        "(session_id, execution_id) -> None. Whatever a run kept is "
        "released, however it ended; a failing handler is logged and the "
        "next one still runs."
    ),
    # ── a worker restart ───────────────────────────────────────────────
    "session_owner": (
        "single: (store, session_id) -> context manager yielding True when "
        "the session's owner lock was taken; restart reconciliation skips a "
        "session another process owns. Without one nothing owns it."
    ),
    "restart_pause": (
        "single: (store, session_id) -> bool. Pause what a run that died with "
        "the previous worker was carrying on; True when something paused."
    ),
    "restart_continuation": (
        "single: an object with locked(session_id), branch(session_id, "
        "request) and check(store, execution, new_execution_id, request) -> "
        "refusal or None, consulted while a restart continues an "
        "interrupted turn as a new one."
    ),
    # ── tool subprocesses ──────────────────────────────────────────────
    "tool_subprocess_reports": (
        "an object with kind, forward(callback) and report(data, *, "
        "execution_id). A tool subprocess forwards each report of ``kind`` "
        "to its parent, which records it for the execution."
    ),
    # ── always-on conversations ────────────────────────────────────────
    "unfinished_work": (
        "(session_id) -> bool. Whether the conversation has work its agent "
        "left unfinished, which a scheduled check-in looks at."
    ),
    "wake": (
        "single: the standing work of always-on conversations, an object with "
        "decide(session_id, spec, config, state, now, reason) -> outcome or "
        "None (the wake taken before any check-in: standing work started or "
        "still running), left_behind(session_id, ended_execution_id) -> bool, "
        "stop(session_id, reason, *, wait_seconds) -> bool, restore(session_id, "
        "*, wait_seconds) -> bool, relabel(session_id, reason, *, wait_seconds) "
        "and summary(session_id) -> dict or None."
    ),
}

SINGLE = frozenset({
    "turn_admission", "verification_turn", "session_owner", "restart_pause",
    "restart_continuation", "wake",
})

_handlers: dict[str, dict[str, Any]] = {moment: {} for moment in MOMENTS}
_lock = RLock()


def register(moment: str, owner: str, handler: Any) -> None:
    """Install ``owner``'s handler for ``moment``; registering again replaces it."""
    if moment not in MOMENTS:
        raise KeyError(f"unknown turn moment {moment!r}")
    with _lock:
        _handlers[moment][owner] = handler


def unregister(moment: str, owner: str) -> None:
    with _lock:
        _handlers.get(moment, {}).pop(owner, None)


def handlers(moment: str) -> list[Any]:
    """The handlers of ``moment`` in registration order, the Programs loaded first."""
    if moment not in MOMENTS:
        raise KeyError(f"unknown turn moment {moment!r}")
    runtime_facts.load_programs()
    with _lock:
        return list(_handlers[moment].values())


def provider(moment: str) -> Any | None:
    """The one handler of a single moment, or None when no Program registered it."""
    if moment not in SINGLE:
        raise KeyError(f"{moment!r} is not a single moment")
    found = handlers(moment)
    return found[-1] if found else None


def snapshot() -> Mapping[str, Mapping[str, Any]]:
    """The current registrations, for tests that restore them."""
    with _lock:
        return {moment: dict(entries) for moment, entries in _handlers.items()}


def restore(values: Mapping[str, Mapping[str, Any]]) -> None:
    with _lock:
        for moment in _handlers:
            _handlers[moment] = dict(values.get(moment, {}))


__all__ = ["MOMENTS", "SINGLE", "handlers", "provider", "register", "restore", "snapshot", "unregister"]
