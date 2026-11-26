"""Tell the agent when a command it left running in the background ends.

As in Claude Code, a background command that exits on its own starts a turn
in its conversation saying so, unless a ``bash`` or ``write_stdin`` reply has
already shown the agent that exit. A conversation that is mid-turn gets the
notice once that turn ends, and the notice turn holds the conversation's run
slot while it runs. The notice turn reuses the conversation's last
chat request, so it has exactly the permissions the user last gave it.
A running Goal has no gap between its turns, so its next turn carries the
notices instead (:func:`claim_for_turn`).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from types import SimpleNamespace

from openprogram.host.processes.store import ProcessStore
from openprogram.runtime.permissions.identity import detached_from_window, inherited_automation

#: The least time between two looks woken by the process records, which
#: change with every line a background command prints.
RECORDS_GAP_SECONDS = 2.0
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


def claim_for_turn(session_id: str, processes: ProcessStore | None = None) -> tuple[list[str], str]:
    """Take the conversation's undelivered exit notices into a turn about to start.

    A running Goal registers its next turn before the previous one lets go
    of the run slot, so the conversation never has the idle gap
    :func:`deliver` waits for; the Goal's next turn carries the notices in
    its own message instead (Claude Code puts a finished background task's
    notification into the next turn the same way). Returns the claimed ids,
    already marked reported, and the text to add; :func:`release_claim`
    gives them back when that turn is not started after all.
    """
    processes = processes or ProcessStore()
    ids, texts = [], []
    for record in processes.unreported_exits():
        if record.get("session_id") != session_id:
            continue
        try:
            text = notice_text(record, processes.output(record["id"]))
            processes.update(record["id"], reported=True)
        except KeyError:
            continue  # pruned meanwhile
        ids.append(record["id"])
        texts.append(text)
    return ids, "\n\n".join(texts)


def release_claim(ids: list[str], processes: ProcessStore | None = None) -> None:
    """The turn that claimed these notices did not start: the sweep delivers them."""
    processes = processes or ProcessStore()
    for process_id in ids:
        try:
            processes.update(process_id, reported=False)
        except KeyError:
            pass


def _latest_chat(executions, store):
    for execution in executions:  # newest first
        payload = store.get_agent_turn_input(execution.execution_id)
        if payload and payload.get("kind") == "chat":
            return execution, payload
    return None, None


def _branch_of_last_reply(session_id: str, branch_id: str | None, source) -> str | None:
    """The branch the notice continues: the one whose line holds the last
    chat turn's reply. That turn's request may name a branch its reply has
    since left (a retry or an edit moved it onto a new one), and the notice
    must be written after that reply, where the conversation goes on."""
    reply = getattr(source, "assistant_message_id", None)
    if not branch_id or not reply:
        return branch_id
    try:
        from openprogram.runtime.context.session_db import default_db
        return default_db().branch_holding(session_id, reply, prefer=branch_id) or branch_id
    except Exception:
        _log.debug("branch of the last reply not read for %s", session_id, exc_info=True)
        return branch_id


def deliver(record: dict, processes: ProcessStore) -> bool:
    """Admit the notice turn; False while the conversation is busy or cannot take one.

    The turn takes its branch's run slot before it is admitted and holds it
    until it ends, like a message the owner types: clients show the
    conversation running, a Stop names the turn, and a Goal's next turn
    waits for it instead of running beside it.
    """
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.conversation.run_control import is_turn_running, is_worker_stopping, run_slot
    from openprogram.runtime.events import emit_ws_frame
    from openprogram.runtime.execution import default_store
    from openprogram.state.store.session.files.session_lock import SessionLockTimeout

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
    branch_id = _branch_of_last_reply(session_id, request.get("branch_id"), source)
    if branch_id:
        request["branch_id"] = branch_id
    try:
        # Never waits: a busy or locked conversation gets the notice at the next sweep.
        if not run_slot.reserve(session_id, message_id, lock_wait=0, branch_id=branch_id):
            return False
    except SessionLockTimeout:
        # Its files are being written: nothing announces the lock's release,
        # so the loop looks again shortly (``RETRY_SECONDS``).
        _retry.set()
        return False
    try:
        adapter = CanonicalAgentAdapter(store=store, event_sink=emit_ws_frame)
        admission = adapter.admit_payload(
            session_id=session_id, payload=dict(payload, request=request),
            trusted_actor=source.trusted_actor, user_message_id=message_id,
            assistant_message_id=f"{message_id}_reply",
            config_snapshot_ref=source.config_snapshot_ref, admission_key=key,
        )
    except BaseException:
        run_slot.release(session_id, message_id)
        raise

    def activate():
        try:
            asyncio.run(adapter.activate(admission))
        except Exception:
            adapter.fail_admission(admission, reason_code="process_exit_activation_failed")
        finally:
            run_slot.finish(session_id, message_id, execution_id=admission.execution_id)

    thread = threading.Thread(target=activate, daemon=True, name="process-exit-notice")
    if not run_slot.activate(session_id, message_id, execution_id=admission.execution_id,
                             status_version=getattr(admission, "status_version", 0), runner=thread):
        adapter.fail_admission(admission, reason_code="process_exit_activation_failed")
        run_slot.release(session_id, message_id)
        # The record is still unreported, so the next sweep delivers the
        # notice again instead of dropping the command's exit.
        return True
    try:
        processes.update(record["id"], reported=True)
    except BaseException:
        # Not marked reported, so the next sweep delivers it again: this
        # admission must not run as well, nor keep the slot it reserved.
        adapter.fail_admission(admission, reason_code="process_exit_activation_failed")
        run_slot.release(session_id, message_id)
        raise
    thread.start()
    return True


def sweep(store: ProcessStore | None = None) -> bool:
    """Deliver due notices, then drop records past retention; whether a
    notice still waits (its conversation is mid-turn)."""
    store = store or ProcessStore()
    waiting = False
    for record in store.unreported_exits():
        try:
            if not deliver(record, store):
                waiting = True
        except Exception:
            waiting = True
            _retry.set()
            _log.warning("process exit notice for %s deferred", record["id"], exc_info=True)
    store.prune()
    return waiting


#: The loop looks on its own only this often; otherwise it waits to be told.
RESYNC_SECONDS = 300.0
#: A delivery that failed or found the conversation's files locked is tried
#: again this soon; nothing announces either ending.
RETRY_SECONDS = 2.0
_retry = threading.Event()
#: Signalled when the process records change: a command's supervisor, a
#: process of its own, writes its exit there.
PROCESSES_TOPIC = "processes"


async def run_exit_notices(stop: asyncio.Event) -> None:
    """Worker loop: deliver due notices until ``stop`` is set.

    It wakes when the process records change (a supervisor wrote an exit)
    and, while a notice waits for its conversation to finish a turn, when a
    conversation may have gone quiet (``run_control/idle.py``). Output
    streaming into the records wakes it often, so after a record change it
    looks at most every ``RECORDS_GAP_SECONDS``.

    Each look also follows every live record's supervisor; one that ends
    without writing its command's end wakes the loop at once, and its
    record turns ``unknown`` (``record_changes.follow_supervisors``)."""
    from openprogram.host._platform import changes
    from openprogram.host.processes import record_changes
    from openprogram.runtime.conversation.run_control.idle import subscribe_idle

    store = ProcessStore()
    changes.watch(store.path.parent, PROCESSES_TOPIC,
                  names={store.path.name, store.path.name + "-wal"})
    loop = asyncio.get_running_loop()
    records_changed = asyncio.Event()
    maybe_quiet = asyncio.Event()
    supervisor_gone = asyncio.Event()
    gone_lock = threading.Lock()
    gone: set[str] = set()
    waiting = False
    sweeping = threading.local()

    def poke(event: asyncio.Event) -> None:
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:  # the loop is closing
            pass

    def maybe_idle() -> None:
        # Only while a notice waits, and not for the sweep's own writes (a
        # notice turn it admits writes executions).
        if waiting and not getattr(sweeping, "on", False):
            poke(maybe_quiet)

    def ended(keys) -> None:
        with gone_lock:
            gone.update(keys)
        poke(supervisor_gone)

    def sweep_once() -> bool:
        sweeping.on = True
        try:
            with gone_lock:
                keys = sorted(gone)
                gone.clear()
            try:
                record_changes.follow_supervisors(store, keys)
            except Exception:  # noqa: BLE001 - the next look follows them again
                _retry.set()
                with gone_lock:
                    gone.update(keys)
                _log.warning("command supervisors not followed", exc_info=True)
            return sweep(store)
        finally:
            sweeping.on = False

    unsubscribes = [
        changes.subscribe(PROCESSES_TOPIC, lambda _keys: poke(records_changed)),
        changes.subscribe(record_changes.SUPERVISORS_TOPIC, ended),
        subscribe_idle(maybe_idle),
    ]
    try:
        while not stop.is_set():
            records_changed.clear()
            maybe_quiet.clear()
            supervisor_gone.clear()
            # Listening while the sweep looks: a turn that ends meanwhile
            # brings another look.
            waiting = True
            _retry.clear()
            waiting = await asyncio.to_thread(sweep_once)
            # Without file events nothing tells this loop of an exit: it
            # looks every RECORDS_GAP_SECONDS.
            timeout = RESYNC_SECONDS if changes.file_events_available() else RECORDS_GAP_SECONDS
            if _retry.is_set():
                timeout = min(timeout, RETRY_SECONDS)
            await _first(stop, (records_changed, maybe_quiet, supervisor_gone), timeout)
            if (records_changed.is_set() and not maybe_quiet.is_set()
                    and not supervisor_gone.is_set() and not stop.is_set()):
                # Output streaming into the records wakes this often; one
                # look per RECORDS_GAP_SECONDS is enough. A conversation
                # going quiet or a supervisor ending meanwhile cuts the gap short.
                await _first(stop, (maybe_quiet, supervisor_gone), RECORDS_GAP_SECONDS)
    finally:
        for unsubscribe in unsubscribes:
            unsubscribe()


async def _first(stop: asyncio.Event, events: tuple[asyncio.Event, ...], timeout: float) -> None:
    """Wait until ``stop`` or one of ``events`` is set, or ``timeout``."""
    tasks = {asyncio.ensure_future(event.wait()) for event in (stop, *events)}
    try:
        await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
