"""Worker thread that wakes always-on conversations when their next wake is due.

A conversation takes part once it is used after its Agent's always-on was
turned on: it joins with its first wake one interval (or, without an
interval, the first step of the wake ladder, an hour) later, so a chat
the person just opened is never interrupted. Conversations older than
that stay quiet unless they are used again. Archiving a conversation
stops its wakes.

The thread sleeps until the earliest wake it found, and wakes before that
when the wake state changes (``store.WAKES_TOPIC``: a conversation's
schedule, an Agent's always-on state or settings, a user's turn) or an
``agent.json`` is written by another process. While something is
unfinished that a tick has to come back to (a running check-in, a run
under a time limit, a Goal that would not pause, a look that failed) it
looks every :data:`TICK_SECONDS` as before. It looks every
:data:`RESYNC_SECONDS` in any case, and every :data:`TICK_SECONDS` where
file events cannot be had.
"""
from __future__ import annotations

import logging
import threading
import time

from openprogram.host._platform import changes

_log = logging.getLogger(__name__)

TICK_SECONDS = 30.0
#: A look even when nothing is due or signalled, in case a signal was lost.
RESYNC_SECONDS = 300.0
# Conversations of one Agent looked at per tick.
SESSIONS_PER_AGENT = 500


class Outlook:
    """What one tick learned about when the next one is needed."""

    def __init__(self) -> None:
        self.next_wake: float | None = None
        #: Something unfinished asks to be looked at again within a tick.
        self.soon = False

    def wake_at(self, at: object, now: float) -> None:
        if isinstance(at, (int, float)) and at > now:
            self.next_wake = at if self.next_wake is None else min(self.next_wake, at)

    def due(self, now: float, interval: float) -> float | None:
        """When the ticker has to look next; ``None`` when only a change can say."""
        if self.soon:
            return now + interval if self.next_wake is None else min(self.next_wake, now + interval)
        return self.next_wake


def _enabled_at(spec, now: float) -> float:
    """When always-on was turned on for this Agent, recorded on first sight.

    The save that turned it on is the Agent's latest update, so a
    conversation started between that save and the next tick still joins.
    """
    from openprogram.runtime.conversation.always_on import store
    state = store.load_agent_state(spec.id)
    enabled_at = state.get("enabled_at")
    if not isinstance(enabled_at, (int, float)) or state.get("stop_unfinished"):
        saved_at = getattr(spec, "updated_at", None)
        enabled_at = min(now, saved_at) if isinstance(saved_at, (int, float)) and saved_at > 0 else now
        store.update_agent_state(spec.id, enabled_at=enabled_at, stop_unfinished=False)
        _adopt_home(spec.id, state, now)
        _restore_stopped(spec.id, now)
    return float(enabled_at)


def _forget_disabled(spec, outlook: Outlook | None = None) -> None:
    from openprogram.runtime.conversation.always_on import store
    if store.load_agent_state(spec.id).get("enabled_at") is None:
        return
    # Kept until every Goal is paused, so a busy one is retried next tick.
    # Turned on before that, it still counts as turned on again.
    if _stop_goals(spec.id):
        store.update_agent_state(spec.id, enabled_at=None, stop_unfinished=False)
    else:
        store.update_agent_state(spec.id, stop_unfinished=True)
        if outlook is not None:
            outlook.soon = True


def _owner_session_ids(agent_id: str) -> list[str]:
    from openprogram.runtime.conversation.always_on.runtime import _db, owner_conversation
    return [str(session["id"]) for session in _db().list_sessions(agent_id=agent_id, limit=SESSIONS_PER_AGENT)
            if owner_conversation(session)]


def _stop_goals(agent_id: str) -> bool:
    """Always-on went off: no automatic Goal of this Agent takes another turn."""
    from openprogram.runtime.conversation.always_on.runtime import OFF_STOP, stop_automatic_goal
    try:
        session_ids = _owner_session_ids(agent_id)
    except Exception:  # noqa: BLE001
        return False
    stopped = True
    for session_id in session_ids:
        try:
            stop_automatic_goal(session_id, OFF_STOP)
        except Exception as exc:  # noqa: BLE001 - one conversation never stops the others
            stopped = False
            _log.warning("goal of %s not paused: %s: %s", session_id, type(exc).__name__, exc)
    return stopped


def _restore_stopped(agent_id: str, now: float) -> None:
    from openprogram.runtime.conversation.always_on.runtime import restore_conversation
    try:
        session_ids = _owner_session_ids(agent_id)
    except Exception:  # noqa: BLE001
        return
    for session_id in session_ids:
        try:
            restore_conversation(session_id, now, turned_on=True)
        except Exception as exc:  # noqa: BLE001 - the Goal stays paused and shows Resume
            _log.warning("goal of %s not restored: %s: %s", session_id, type(exc).__name__, exc)


def _adopt_home(agent_id: str, state: dict, now: float) -> None:
    """Older versions kept one home conversation per Agent: it keeps waking."""
    from openprogram.runtime.conversation.always_on import store
    from openprogram.runtime.conversation.always_on.runtime import _db
    sid = state.get("home_session_id")
    if not sid or not _db().get_session(sid) or store.load_state(sid):
        return
    store.update_state(sid, joined_at=now, **{
        key: state[key] for key in ("applied_goal", "last_check_in_at", "last_wake", "next_wake_at")
        if state.get(key) is not None})


def _still_owner_conversation(session_id: str) -> bool:
    """The list rows can be older than the conversation: a Try chat indexed
    before its flag was, for one. Read the conversation itself before acting."""
    from openprogram.runtime.conversation.always_on.runtime import _db, owner_conversation
    try:
        return owner_conversation(_db().get_session(session_id))
    except Exception:  # noqa: BLE001 - looked at again next tick
        return False


def _session_is_due(session: dict, config: dict, enabled_at: float, now: float,
                    outlook: Outlook | None = None) -> bool:
    """Bring one conversation's wake state up to date; True when its wake has passed.

    ``outlook`` learns when this conversation wakes next."""
    outlook = outlook or Outlook()
    from openprogram.runtime.conversation.always_on import store
    from openprogram.runtime.conversation.always_on.runtime import (
        owner_conversation, reap_interrupted, schedule_next)
    sid = str(session["id"])
    if not owner_conversation(session) or store.directory(sid) is None:
        return False
    state = store.load_state(sid)
    if state.get("check_in"):
        # A check-in an earlier worker ran was cut off by the restart.
        state = reap_interrupted(sid)
        if state.get("check_in"):
            # Still running: its time limit is looked at every tick.
            outlook.soon = True
    if not state:
        if (session.get("updated_at") or 0) >= enabled_at and _still_owner_conversation(sid):
            outlook.wake_at(schedule_next(sid, config, now, joined_at=now, wake_step=0).get("next_wake_at"), now)
        return False
    next_wake = state.get("next_wake_at")
    if not isinstance(next_wake, (int, float)) or next_wake < enabled_at:
        # Turned on again after a pause: start counting from now.
        if _still_owner_conversation(sid):
            outlook.wake_at(schedule_next(sid, config, now, wake_step=0).get("next_wake_at"), now)
        return False
    outlook.wake_at(next_wake, now)
    return next_wake <= now and _still_owner_conversation(sid)


def _due_for_agent(spec, now: float, outlook: Outlook | None = None) -> list[str]:
    from openprogram.runtime.conversation.always_on.runtime import _db
    outlook = outlook or Outlook()
    config = spec.always_on or {}
    if not config.get("enabled"):
        _forget_disabled(spec, outlook)
        return []
    enabled_at = _enabled_at(spec, now)
    due = []
    for session in _db().list_sessions(agent_id=spec.id, limit=SESSIONS_PER_AGENT):
        try:
            if _session_is_due(session, config, enabled_at, now, outlook):
                due.append(str(session["id"]))
        except Exception as exc:  # noqa: BLE001 - one conversation never stops the others
            outlook.soon = True
            _log.warning("always-on conversation %s skipped this tick: %s: %s",
                         session.get("id"), type(exc).__name__, exc, exc_info=True)
    return due


def due_sessions(now: float, outlook: Outlook | None = None) -> list[str]:
    """Conversations of always-on Agents whose next wake has passed.

    ``outlook`` learns when the earliest of the others wakes."""
    from openprogram.runtime.agent_registry import manager
    outlook = outlook or Outlook()
    due = []
    for agent_id in manager.list_ids():
        try:
            spec = manager.get(agent_id)
            if spec is not None:
                due.extend(_due_for_agent(spec, now, outlook))
        except Exception as exc:  # noqa: BLE001 - one Agent never stops the others
            outlook.soon = True
            _log.warning("always-on Agent %s skipped this tick: %s: %s",
                         agent_id, type(exc).__name__, exc, exc_info=True)
    return due


def tick(now: float | None = None, outlook: Outlook | None = None) -> list[dict]:
    """Stop runs past their limit and wake the conversations that are due.

    ``outlook`` learns when the next tick is needed."""
    from openprogram.runtime.conversation.always_on.runtime import wake
    from openprogram.runtime.scheduler import time_limits

    results = []
    outlook = outlook or Outlook()
    now = time.time() if now is None else now
    try:
        # First, so a check-in stopped here is recorded by the look below.
        time_limits.stop_overdue(now)
    except Exception as exc:  # noqa: BLE001 - wakes go on without it
        outlook.soon = True
        _log.warning("time limits not checked: %s: %s", type(exc).__name__, exc, exc_info=True)
    for session_id in due_sessions(now, outlook):
        try:
            results.append(wake(session_id, reason="schedule", now=now))
        except Exception as exc:  # noqa: BLE001 - one conversation never stops the others
            _log.warning("wake %s failed: %s: %s", session_id, type(exc).__name__, exc, exc_info=True)
    try:
        # A run under a time limit (a check-in just started, a scheduled run)
        # is looked at every tick until it ends.
        if time_limits.limited_runs_open():
            outlook.soon = True
    except Exception:  # noqa: BLE001
        outlook.soon = True
    return results


def _watch_agents() -> None:
    """``agent.json`` written by another process (the CLI) wakes the ticker."""
    from openprogram.runtime.agent_registry.manager import agent_dir
    from openprogram.runtime.conversation.always_on.store import WAKES_TOPIC
    changes.watch(agent_dir("_").parent, WAKES_TOPIC, recursive=True, names={"agent.json"})


class _Stop(threading.Event):
    """The ticker's stop: setting it also ends the ticker's wait."""

    def __init__(self, listener: changes.Listener) -> None:
        super().__init__()
        self._listener = listener

    def set(self) -> None:
        super().set()
        self._listener.wake()


def start_in_worker(interval: float = TICK_SECONDS) -> tuple[threading.Event, threading.Thread]:
    from openprogram.runtime.conversation.always_on import handoff_migration
    try:
        # What the removed handoff tool left goes through send_message now.
        handoff_migration.migrate_in_worker()
    except Exception as exc:  # noqa: BLE001
        _log.warning("stored handoffs not migrated: %s: %s", type(exc).__name__, exc, exc_info=True)
    from openprogram.runtime.conversation.always_on.store import WAKES_TOPIC
    listener = changes.listen(WAKES_TOPIC)
    stop = _Stop(listener)
    started_at = time.time()
    try:
        _watch_agents()
    except Exception as exc:  # noqa: BLE001 - this process's own changes still wake it
        _log.warning("agent files not watched: %s: %s", type(exc).__name__, exc, exc_info=True)

    def run():
        # After one interval, so chats cut off by the restart are running
        # again and a report waits for them.
        if stop.wait(interval):
            return
        try:
            from openprogram.runtime.conversation.always_on.runtime import recover_reports
            recover_reports(started_at)
        except Exception as exc:  # noqa: BLE001
            _log.warning("reports not recovered: %s: %s", type(exc).__name__, exc, exc_info=True)
        while not stop.is_set():
            outlook = Outlook()
            try:
                tick(outlook=outlook)
            except Exception as exc:  # noqa: BLE001
                outlook.soon = True
                _log.warning("tick failed: %s: %s", type(exc).__name__, exc, exc_info=True)
            changes.wait_for(listener, resync=RESYNC_SECONDS, poll=interval,
                             due=outlook.due(time.time(), interval))

    thread = threading.Thread(target=run, name="always-on-ticker", daemon=True)
    thread.start()
    return stop, thread
