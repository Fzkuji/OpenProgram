"""What happens after an execution event is written.

Each lifecycle event closes or updates the assistant message it belongs to and
tells connected clients. A single thread in the worker follows the event table
in memory; nothing about delivery is written down. Effects are idempotent and
guarded by the execution's current version, so running one again is harmless.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

from .model import ExecutionEvent, ExecutionRecord, TERMINAL_EXECUTION_STATUSES


_RUNNING_STATUSES = {"queued", "running", "pausing", "cancelling"}
_LIFECYCLE_KINDS = {"execution.created", "execution.updated"}
_RETRY_LIMIT = 5
_RETRY_DELAY_SECONDS = 1.0
_CATCH_UP_WINDOW_SECONDS = 3600.0
_WAIT_FAILURES = {"wait_declined", "wait_timeout"}
_OPEN_ROW_STATUSES = {"pending", "running", "streaming", "paused", "cancelling"}
_ROW_STATUS_AT_END = {
    "completed": "completed", "failed": "error",
    "cancelled": "cancelled", "interrupted": "interrupted",
}
_workers: dict[str, "EventEffectsWorker"] = {}
_workers_lock = threading.Lock()
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunningExecution:
    execution_id: str
    session_id: str
    status: str
    payload: Mapping[str, Any]
    updated_at: float


class ExecutionEventEffects:
    """Applies the effects of events, in order, following an in-memory cursor."""

    def __init__(self, store, *, after: int | None = None):
        self.store = store
        self._cursor = store.latest_global_event_sequence() if after is None else after
        self._retries: dict[int, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def drain(self, *, limit: int = 500) -> int:
        """Apply every event written since the last call; return how many."""
        with self._lock:
            applied = self._retry_due()
            while True:
                events = self.store.list_events_after(self._cursor, limit=limit)
                for event in events:
                    self._cursor = event.sequence
                    self._attempt(event)
                    applied += 1
                if len(events) < limit:
                    return applied

    def _attempt(self, event: ExecutionEvent, *, failures: int = 0) -> None:
        try:
            self.apply(event)
        except Exception:
            failures += 1
            if failures >= _RETRY_LIMIT:
                _log.exception("effect of execution event %s gave up", event.sequence)
                self._retries.pop(event.sequence, None)
            else:
                _log.debug("effect of execution event %s will retry", event.sequence, exc_info=True)
                self._retries[event.sequence] = (failures, time.monotonic() + _RETRY_DELAY_SECONDS * failures)
        else:
            self._retries.pop(event.sequence, None)

    def seconds_to_next_retry(self) -> float | None:
        """How long until the earliest failed effect is tried again."""
        with self._lock:
            if not self._retries:
                return None
            ready = min(ready_at for _failures, ready_at in self._retries.values())
        return max(0.0, ready - time.monotonic())

    def _retry_due(self) -> int:
        now = time.monotonic()
        due = [(sequence, failures) for sequence, (failures, ready_at) in self._retries.items() if ready_at <= now]
        for sequence, failures in due:
            event = self.store.get_event_by_sequence(sequence)
            if event is None:
                self._retries.pop(sequence, None)
            else:
                self._attempt(event, failures=failures)
        return len(due)

    def apply(self, event: ExecutionEvent, *, notify_clients: bool = True) -> None:
        if event.kind not in _LIFECYCLE_KINDS and event.kind not in {"command.applied", "command.rejected"}:
            return
        if notify_clients:
            self._publish_steer_outcome(event)
        if event.kind not in _LIFECYCLE_KINDS:
            return
        execution = self.store.execution_snapshot_at(event.execution_id, event.sequence)
        if execution is None:
            return
        if ((execution.status.value == "failed"
             and execution.reason_code in {"agent_runner_error", *_WAIT_FAILURES})
                or (execution.status.value == "paused"
                    and execution.reason_code == "continuation_contract_mismatch")):
            self._project_failed_assistant(execution)
        if execution.status.value in {"running", "completed"}:
            self._project_recovered_assistant(execution)
        if execution.status.value == "cancelled":
            self.project_cancelled_assistant(execution)
        # The running turn reports every other ending; these can have no turn
        # left (a stop lands on a paused run with no owner).
        if execution.status.value in {"interrupted", "cancelled"} or (
                execution.status.value == "failed" and execution.reason_code in _WAIT_FAILURES):
            from . import hooks

            hooks.close_ownerless_run(self.store, execution)
        self._project_row_status(execution)
        if execution.status in TERMINAL_EXECUTION_STATUSES:
            self._settle_session_status(execution)
        if notify_clients:
            from openprogram.runtime.events import emit_ws_frame
            from openprogram.runtime.execution.projections.public import execution_update_frame

            self._announce_owned_state(execution)
            payload = self._payload(event, execution)
            emit_ws_frame(execution_update_frame(
                payload["execution"], payload["event_cursor"], data=payload,
            ))

    def catch_up(self, *, window_seconds: float = _CATCH_UP_WINDOW_SECONDS) -> int:
        """Finish what a previous worker wrote but did not get to apply."""
        from . import hooks

        count = 0
        for event in self.store.latest_lifecycle_events(since=time.time() - window_seconds):
            try:
                self.apply(event, notify_clients=False)
                execution = self.store.get_execution(event.execution_id)
                if execution is not None and execution.status in TERMINAL_EXECUTION_STATUSES:
                    hooks.settle_terminal(self.store, execution)
                count += 1
            except Exception:
                _log.exception("startup effect of execution %s failed", event.execution_id)
        return count

    def list_running(self, *, session_id: str | None = None) -> list[RunningExecution]:
        running = []
        for execution in self.store.list_nonterminal(session_id=session_id):
            if execution.status.value not in _RUNNING_STATUSES:
                continue
            event = self.store.latest_lifecycle_event(execution.execution_id)
            if event is None:
                continue
            running.append(RunningExecution(
                execution_id=execution.execution_id, session_id=execution.session_id,
                status=execution.status.value, payload=self._payload(event, execution),
                updated_at=execution.updated_at,
            ))
        running.sort(key=lambda item: (-item.updated_at, item.execution_id))
        return running

    def _payload(self, event: ExecutionEvent, execution: ExecutionRecord) -> dict[str, Any]:
        from .projections.foreground import foreground_task_snapshot

        source = self.store.get_execution_input(execution.execution_id)
        entrypoint = source.entrypoint if source is not None else None
        return {
            "event_sequence": event.execution_sequence,
            "event_cursor": {
                "execution_id": execution.execution_id,
                "next_sequence": event.execution_sequence + 1,
                "snapshot_status_version": execution.status_version,
            },
            "event": {
                "sequence": event.execution_sequence,
                "kind": event.kind,
                "execution_version": event.execution_version,
                "command_id": event.command_id,
                "created_at": event.created_at,
            },
            "execution": execution.to_dict(),
            "input": {
                "entrypoint": entrypoint,
                "user_message_id": source.user_message_id if source else None,
                "assistant_message_id": source.assistant_message_id if source else None,
            },
            "foreground_task": foreground_task_snapshot(self.store, execution),
            "ui": {
                "is_running": execution.status.value in _RUNNING_STATUSES,
                "label": entrypoint or "execution",
            },
        }

    def _announce_owned_state(self, execution: ExecutionRecord) -> None:
        """What a run's state decides changed with it: the resources list of
        the conversations above it (a page's control, a run's membership)
        and their open system-access waits. Every run's lifecycle passes
        here, also one written by an agentic subprocess."""
        try:
            from openprogram.host._platform import changes
            from openprogram.host.resources import conversation_changes
            from .conversation_scope import sessions_above_execution
            from .waits import SYSTEM_ACCESS_WAITS_TOPIC

            sessions = sessions_above_execution(self.store, execution)
            conversation_changes.changed(*sessions, stored=False)
            changes.signal(SYSTEM_ACCESS_WAITS_TOPIC, *sessions)
        except Exception:  # noqa: BLE001 - windows read again when next shown
            _log.debug("run change not announced", exc_info=True)

    @staticmethod
    def _publish_steer_outcome(event: ExecutionEvent) -> None:
        """Tell clients when a steer is applied or closed unapplied.

        A safe point applies a steer when its message is written into the
        turn; a turn that ends first rejects it. Either way the submitting
        client learns the outcome by command id from this frame. The
        command's payload (message, attachments) is left out.
        """
        if event.kind not in {"command.applied", "command.rejected"}:
            return
        command = (event.payload or {}).get("command")
        if not isinstance(command, dict) or command.get("kind") != "execution.steer":
            return
        from openprogram.runtime.events import emit_ws_frame

        public = {key: value for key, value in command.items()
                  if key not in {"payload", "actor"}}
        emit_ws_frame({
            "type": "execution.command.updated", "command": public,
            "data": {"command": public},
        })

    def project_cancelled_assistant(self, execution: ExecutionRecord) -> None:
        """Close this turn's display without changing its text or effect receipts.

        Control invokes this immediately; the event effect covers every other path.
        """
        from openprogram.runtime.context.session_db import default_db
        from openprogram.state.store import SessionNodeWriter

        current = self.store.get_execution(execution.execution_id)
        source = self.store.get_execution_input(execution.execution_id)
        if (current is None or current.status_version != execution.status_version
                or current.status.value != "cancelled"
                or source is None or not source.assistant_message_id):
            return
        if execution.parent_execution_id:
            parent = self.store.get_execution_input(execution.parent_execution_id)
            if parent is not None and parent.assistant_message_id == source.assistant_message_id:
                return
        db = default_db()
        writer = SessionNodeWriter(db, execution.session_id, advance_head=False)
        graph = writer.load()
        node = graph.nodes.get(source.assistant_message_id)
        if node is None:
            node = self._reply_of_turn_that_never_ran(execution, source, writer, graph)
        if node is not None:
            self._close_cancelled_reply(execution, source, writer, db, node)

    def _reply_of_turn_that_never_ran(self, execution: ExecutionRecord, source, writer, graph):
        """The stopped reply of a chat turn a stop reached before it started.

        A chat turn opens its reply when it starts, so one stopped while
        queued, or after its lease but before a driver was bound to run it,
        has none and never will: no attempt can start on a cancelled
        execution. Its message was
        written before it was admitted, so the reply is written here, as
        stopped, as a turn stopped just after it started leaves it. A chat
        turn that writes its own message when it starts showed nothing and
        leaves nothing to close (None). Any other anchor is written by
        whoever admitted the execution and can still arrive, and a turn that
        did start and left no reply has lost it: both raise, and the event
        effect tries again until it gives up loudly.
        """
        from .attempts import AttemptStore

        waiting = RuntimeError("Cancelled execution is waiting for its assistant node")
        turn_input = self.store.get_agent_turn_input(execution.execution_id) or {}
        # An attempt a stop ended before any driver was bound never ran, even
        # if it was activated.
        if turn_input.get("kind") != "chat" or any(
                attempt.activated_at is not None and attempt.outcome != "cancelled_during_activation"
                for attempt in AttemptStore(self.store).list_for_execution(execution.execution_id)):
            raise waiting
        request = turn_input.get("request") or {}
        if not request.get("user_already_persisted"):
            return None
        if not source.user_message_id or source.user_message_id not in graph.nodes:
            raise waiting
        from openprogram.state.authority import runtime_authority
        from openprogram.state.store.session.nodes import Call, ROLE_LLM

        # It never ran: it starts and ends at the stop.
        stopped_at = execution.terminal_at or execution.updated_at
        metadata = {
            "source": request.get("source") or "",
            "status": "cancelled",
            "started_at": stopped_at,
            "finished_at": stopped_at,
            **runtime_authority(request, f"agent/{request.get('agent_id')}"),
        }
        writer.append(Call(
            id=source.assistant_message_id, created_at=stopped_at, role=ROLE_LLM, output="",
            predecessor=source.user_message_id, metadata=metadata,
        ), create_if_missing=False)
        return writer.load().nodes.get(source.assistant_message_id)

    def _close_cancelled_reply(self, execution: ExecutionRecord, source, writer, db, node) -> None:
        metadata = node.metadata or {}
        if metadata.get("status") not in {None, "running", "paused", "interrupted", "error", "cancelled"}:
            return
        if metadata.get("status") != "cancelled":
            writer.update(source.assistant_message_id, metadata={
                "status": "cancelled", "finished_at": execution.terminal_at,
                "error": None, "error_type": None,
            })
        # A cancelled owner may not reach TurnWriter.finalize. Complete only
        # this root turn's head transition; never rewind a later turn or branch.
        turn_input = self.store.get_agent_turn_input(execution.execution_id)
        request = (turn_input or {}).get("request") or {}
        if (not execution.parent_execution_id and turn_input is not None
                and request.get("advance_head", True)
                and source.user_message_id and not node.caller
                and db.has_persisted_ancestor(execution.session_id, source.user_message_id, node.id)):
            db.compare_and_set_head(
                execution.session_id, source.user_message_id, source.assistant_message_id,
            )
        if not any(item.status.value in _RUNNING_STATUSES
                   for item in self.store.list_nonterminal(session_id=execution.session_id)):
            if (db.get_session(execution.session_id) or {}).get("status") != "idle":
                db.update_session(execution.session_id, status="idle")

    def _settle_session_status(self, execution: ExecutionRecord) -> None:
        """A chat turn has ended: a conversation it left ``running`` behind
        another turn is ``done`` once no such turn is left (``settle_session_status``)."""
        if execution.parent_execution_id:
            return
        current = self.store.get_execution(execution.execution_id)
        if current is None or current.status not in TERMINAL_EXECUTION_STATUSES:
            return
        if (self.store.get_agent_turn_input(execution.execution_id) or {}).get("kind") != "chat":
            return
        from openprogram.runtime.context.session_db import default_db
        from .projections.foreground import settle_session_status

        settle_session_status(default_db(), self.store, execution.session_id)

    def _project_row_status(self, execution: ExecutionRecord) -> None:
        """Keep the anchor row's status with the run: settled at its end,
        paused while it waits, and running again when it resumes."""
        status = execution.status.value
        if status != "running" and status != "paused" and status not in _ROW_STATUS_AT_END:
            return
        if status == "paused" and execution.reason_code == "continuation_contract_mismatch":
            return
        from openprogram.runtime.context.session_db import default_db
        from openprogram.state.store import SessionNodeWriter

        current = self.store.get_execution(execution.execution_id)
        source = self.store.get_execution_input(execution.execution_id)
        if (current is None or current.status_version != execution.status_version
                or source is None or not source.assistant_message_id):
            return
        if execution.parent_execution_id:
            parent = self.store.get_execution_input(execution.parent_execution_id)
            if parent is not None and parent.assistant_message_id == source.assistant_message_id:
                return
        writer = SessionNodeWriter(default_db(), execution.session_id, advance_head=False)
        node = writer.load().nodes.get(source.assistant_message_id)
        if node is None:
            return
        row_status = (node.metadata or {}).get("status")
        if status == "paused":
            if row_status in {None, "pending", "running", "streaming"}:
                writer.update(source.assistant_message_id, metadata={
                    "status": "paused", "pause_reason": execution.reason_code,
                })
        elif status == "running":
            if row_status == "paused":
                writer.update(source.assistant_message_id, metadata={
                    "status": "running", "pause_reason": None,
                })
        elif row_status in _OPEN_ROW_STATUSES:
            writer.update(source.assistant_message_id, metadata={
                "status": _ROW_STATUS_AT_END[status],
                "finished_at": execution.terminal_at or execution.updated_at,
            })

    def _project_recovered_assistant(self, execution: ExecutionRecord) -> None:
        """Clear a prior continuation error only after canonical recovery."""
        from openprogram.runtime.context.session_db import default_db
        from openprogram.state.store import SessionNodeWriter

        current = self.store.get_execution(execution.execution_id)
        source = self.store.get_execution_input(execution.execution_id)
        if (current is None or current.status_version != execution.status_version
                or source is None or not source.assistant_message_id):
            return
        if execution.parent_execution_id:
            parent = self.store.get_execution_input(execution.parent_execution_id)
            if parent is not None and parent.assistant_message_id == source.assistant_message_id:
                return
        writer = SessionNodeWriter(default_db(), execution.session_id, advance_head=False)
        node = writer.load().nodes.get(source.assistant_message_id)
        if node is None or (node.metadata or {}).get("error") != "continuation_contract_mismatch":
            return
        writer.update(source.assistant_message_id, metadata={
            "status": execution.status.value, "error": None, "error_type": None,
            "finished_at": execution.terminal_at,
        })

    def _project_failed_assistant(self, execution: ExecutionRecord) -> None:
        from openprogram.runtime.context.session_db import default_db
        from openprogram.state.store import SessionNodeWriter

        current = self.store.get_execution(execution.execution_id)
        source = self.store.get_execution_input(execution.execution_id)
        if (current is None or current.status_version != execution.status_version
                or source is None or not source.assistant_message_id):
            return
        if execution.parent_execution_id:
            parent = self.store.get_execution_input(execution.parent_execution_id)
            if parent is not None and parent.assistant_message_id == source.assistant_message_id:
                return  # A legacy shared anchor cannot authorize a parent write.
        writer = SessionNodeWriter(default_db(), execution.session_id, advance_head=False)
        node = writer.load().nodes.get(source.assistant_message_id)
        if node is None or (node.metadata or {}).get("status") not in {
            None, "running", "error", "interrupted", "paused",
        }:
            return
        reason = execution.reason_code
        if (reason == "continuation_contract_mismatch"
                and (node.metadata or {}).get("error") == reason
                and (node.metadata or {}).get("status") == "error"):
            return
        fields = {"metadata": {"status": "error", "error": reason,
                               "finished_at": execution.terminal_at or execution.updated_at}}
        if reason == "continuation_contract_mismatch":
            notice = "Execution could not resume. The pending operation was not run."
            fields["output"] = f"{node.output}\n\n{notice}".strip()
            writer.update(source.assistant_message_id, **fields)
            return
        if reason in {"wait_declined", "wait_timeout"}:
            notice = ("This operation was declined and was not executed."
                      if reason == "wait_declined" else "This request expired before an answer was received.")
            blocks = self._checkpoint_blocks(execution, source.assistant_message_id)
            if blocks:
                from openprogram.state.reply_text import reply_text
                # The notice is the runtime's, never part of the passage before it.
                fields["output"] = "\n\n".join(part for part in (reply_text(blocks), notice) if part)
                blocks.append({"type": "text", "text": notice})
                fields["metadata"]["extra"] = json.dumps({"blocks": blocks})
            else:
                fields["output"] = node.output if notice in (node.output or "") else f"{node.output or ''}\n\n{notice}".strip()
            if reason == "wait_declined":
                fields["metadata"].update(status="cancelled", error=None, error_type=None)
            writer.update(source.assistant_message_id, **fields)
            return
        if not node.output:
            output = ""
            state = self._journal(execution, source.assistant_message_id)
            if state is not None:
                from openprogram.state.reply_text import reply_text
                output = reply_text(item for item in state.last.message.get("content", [])
                                    if isinstance(item, dict))
            fields["output"] = output or "[error] Agent execution failed."
        writer.update(source.assistant_message_id, **fields)

    def _journal(self, execution: ExecutionRecord, assistant_id: str):
        """The turn's journal, if it recorded a model decision for this reply."""
        from .agent_journal import latest_pointer, load_journal
        from .recovery.checkpoints import ExecutionCheckpointStore

        if not execution.session_id:
            return None
        try:
            head = execution.checkpoint_head_id or execution.source_checkpoint_id
            checkpoint = ExecutionCheckpointStore(self.store).get(head) if head else None
            base = checkpoint.state_refs.get("agent_journal") if checkpoint is not None else None
            pointer = latest_pointer(execution.session_id, execution.execution_id, base)
            state = load_journal(execution.session_id, pointer) if pointer else None
        except Exception:
            _log.warning("Could not read the journal of %s", execution.execution_id, exc_info=True)
            return None
        if state is None or not state.has_decision or state.turn.get("assistant_message_id") != assistant_id:
            return None
        return state

    def _checkpoint_blocks(self, execution: ExecutionRecord, assistant_id: str) -> list[dict]:
        """Recover only the public display blocks from this turn's journal."""
        state = self._journal(execution, assistant_id)
        if state is None:
            return []
        blocks = state.display_blocks()
        tools = {
            tool_id: block
            for block in blocks
            if block.get("type") == "tool"
            and isinstance((tool_id := block.get("tool_call_id")), str)
            and tool_id
        }
        for call in state.last.tool_calls[len(state.tool_results()):]:
            tool = tools.get(str(call.get("id")))
            if tool is not None and "result" not in tool:
                tool.update(result="Not executed: request declined." if execution.reason_code == "wait_declined"
                            else "Not executed: request expired.", is_error=True)
        return blocks


class EventEffectsWorker:
    """The one thread applying event effects for one execution store."""

    def __init__(self, store, *, idle_wait_seconds: float = 300.0):
        self.effects = ExecutionEventEffects(store)
        self.idle_wait_seconds = idle_wait_seconds
        self._wake = threading.Event()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, name="openprogram-event-effects", daemon=True)

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, *, timeout: float = 1.0) -> bool:
        self._stopped.set()
        self._wake.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=timeout)
        return not self.is_alive

    def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                self.effects.drain()
            except Exception:
                _log.exception("execution event effects failed")
            # Every commit that writes an event wakes this thread
            # (wake_event_effects), and only this process writes them: the
            # wait ends at the next retry of a failed effect, or is a
            # safety net.
            retry = self.effects.seconds_to_next_retry()
            self._wake.wait(self.idle_wait_seconds if retry is None
                            else min(self.idle_wait_seconds, retry))
            self._wake.clear()


def _worker_key(store) -> str:
    return str(store.path.resolve())


def start_event_effects(store) -> EventEffectsWorker:
    """Start the one effects thread for this execution store."""
    key = _worker_key(store)
    with _workers_lock:
        existing = _workers.get(key)
        if existing is not None and existing.is_alive:
            return existing
        worker = EventEffectsWorker(store)
        _workers[key] = worker
        worker.start()
        return worker


def wake_event_effects(path) -> None:
    """Have the effects thread look now, after a commit in this process."""
    with _workers_lock:
        worker = _workers.get(str(path.resolve()))
    if worker is not None:
        worker.wake()


def stop_event_effects(store=None) -> bool:
    """Stop the selected effects threads; return whether all of them ended."""
    with _workers_lock:
        if store is None:
            entries = list(_workers.items())
        else:
            key = _worker_key(store)
            entries = [(key, _workers[key])] if key in _workers else []
        stopped = True
        for key, worker in entries:
            if worker.stop():
                _workers.pop(key, None)
            else:
                stopped = False
        return stopped


def list_running_executions() -> list[RunningExecution]:
    """The executions the Running surface shows, newest change first."""
    from .store import default_store

    return ExecutionEventEffects(default_store()).list_running()
