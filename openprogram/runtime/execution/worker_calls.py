"""Work a subprocess hands to the process that owns the run tables.

A function subprocess only reads the run tables. Starting a sub-agent or
recording an Auto verdict changes them, so the subprocess asks its parent to do
it and waits for the answer. The two sides meet on the subprocess's own queues:
a call goes up the event queue, its answer comes down the answer queue.

A scheduled run writes its own turn's rows, but its sub-agents go the same way:
only the process holding the Worker lock can claim a queued Job, and it cannot
see a Job that lives in another process's memory.

Only the functions named in ``_WORKER_FUNCTIONS`` can be called this way. They
are named by module path because this package sits below the ones that hold them.
"""
from __future__ import annotations

import contextvars
import importlib
import logging
import pickle
import threading
import uuid
from concurrent.futures import Future
from typing import Any, Callable

_log = logging.getLogger(__name__)

_CALL = "__op_worker_call__"
_ANSWER = "__op_worker_answer__"
_CALLBACK = "__op_worker_callback__"
_CALLBACK_DONE = "__op_worker_callback_done__"

_WORKER_FUNCTIONS = frozenset({
    "openprogram.runtime.jobs.runner.worker_runner:call_runner",
    "openprogram.runtime.permissions.auto_history:record_in_worker",
})

_PROCESS_CHECK_SECONDS = 1.0


class WorkerUnavailable(RuntimeError):
    """The other side of the subprocess's queues is gone."""


class SubprocessWorkerCalls:
    """The subprocess's side: send a call, wait for its answer."""

    def __init__(self, send: Callable[[dict], None]):
        self._send = send
        self._lock = threading.Lock()
        self._pending: dict[str, Future] = {}
        self._callbacks: dict[str, tuple[contextvars.Context, Callable]] = {}
        self._gone: WorkerUnavailable | None = None

    def call(self, function: str, *args: Any, callback: Callable | None = None, **kwargs: Any) -> Any:
        """Run ``function`` in the parent. ``callback`` is run here, once, if the parent asks."""
        call_id = uuid.uuid4().hex
        answer: Future = Future()
        with self._lock:
            if self._gone is not None:
                raise self._gone
            self._pending[call_id] = answer
            if callback is not None:
                self._callbacks[call_id] = (contextvars.copy_context(), callback)
        try:
            self._send({_CALL: True, "id": call_id, "function": function,
                        "payload": pickle.dumps((args, kwargs)), "has_callback": callback is not None})
            return answer.result()
        finally:
            with self._lock:
                self._pending.pop(call_id, None)
                self._callbacks.pop(call_id, None)

    def parent_gone(self, reason: str) -> None:
        """End every waiting call, and any later one, with ``reason``."""
        with self._lock:
            self._gone = WorkerUnavailable(reason)
            waiting = list(self._pending.values())
        for answer in waiting:
            if not answer.done():
                answer.set_exception(self._gone)

    def handle_message(self, message: Any) -> bool:
        """Consume a message from the answer queue; False if it is not one of these."""
        if not isinstance(message, dict):
            return False
        if message.get(_ANSWER):
            with self._lock:
                answer = self._pending.get(message["id"])
            if answer is not None:
                succeeded, value = pickle.loads(message["payload"])
                answer.set_result(value) if succeeded else answer.set_exception(value)
            return True
        if message.get(_CALLBACK):
            threading.Thread(target=self._run_callback, args=(message,), daemon=True).start()
            return True
        return False

    def _run_callback(self, message: dict) -> None:
        with self._lock:
            held = self._callbacks.get(message["id"])
        error = None
        try:
            if held is None:
                raise WorkerUnavailable("the call that asked for this callback has ended")
            context, callback = held
            context.copy().run(callback, *pickle.loads(message["payload"]))
        except Exception as exc:  # noqa: BLE001 - reported to the parent, which raises it
            error = f"{type(exc).__name__}: {exc}"
        self._send({_CALLBACK_DONE: True, "id": message["id"], "error": error})


class WorkerCallServer:
    """The parent's side for one subprocess."""

    def __init__(self, answer_queue, is_alive: Callable[[], bool]):
        self._answers = answer_queue
        self._is_alive = is_alive
        # Sub-agents started here belong to the turn that started the subprocess.
        self._context = contextvars.copy_context()
        self._lock = threading.Lock()
        self._callbacks: dict[str, dict] = {}

    def handle_message(self, message: Any) -> bool:
        """Consume a message from the event queue; False if it is not one of these."""
        if not isinstance(message, dict):
            return False
        if message.get(_CALL):
            threading.Thread(target=self._context.copy().run, args=(self._answer, message),
                             daemon=True, name="function-worker-call").start()
            return True
        if message.get(_CALLBACK_DONE):
            with self._lock:
                waiting = self._callbacks.get(message["id"])
            if waiting is not None:
                waiting["error"] = message.get("error")
                waiting["done"].set()
            return True
        return False

    def _answer(self, message: dict) -> None:
        call_id = message["id"]
        try:
            args, kwargs = pickle.loads(message["payload"])
            if message.get("has_callback"):
                kwargs["callback"] = lambda *values: self._ask_callback(call_id, values)
            result = (True, _worker_function(message["function"])(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001 - raised again in the subprocess
            result = (False, exc)
        try:
            payload = _encoded(result)
            self._answers.put({_ANSWER: True, "id": call_id, "payload": payload})
        except Exception:  # noqa: BLE001 - the subprocess is gone with its queue
            _log.debug("worker call answer not delivered", exc_info=True)

    def _ask_callback(self, call_id: str, values: tuple) -> None:
        waiting = {"done": threading.Event(), "error": None}
        with self._lock:
            self._callbacks[call_id] = waiting
        try:
            self._answers.put({_CALLBACK: True, "id": call_id, "payload": pickle.dumps(values)})
            while not waiting["done"].wait(_PROCESS_CHECK_SECONDS):
                if not self._is_alive():
                    raise WorkerUnavailable("the function subprocess exited during its callback")
        finally:
            with self._lock:
                self._callbacks.pop(call_id, None)
        if waiting["error"]:
            raise RuntimeError(waiting["error"])


def _worker_function(name: str) -> Callable:
    if name not in _WORKER_FUNCTIONS:
        raise PermissionError(f"{name!r} cannot be called from a function subprocess")
    module, attribute = name.split(":")
    return getattr(importlib.import_module(module), attribute)


def _encoded(result: tuple[bool, Any]) -> bytes:
    """Pickle an answer now, where a value that cannot cross becomes an error the caller sees."""
    try:
        payload = pickle.dumps(result)
        pickle.loads(payload)
        return payload
    except Exception:  # noqa: BLE001 - any pickling failure means the same thing
        value = result[1]
        described = f"{type(value).__name__}: {value}"
        return pickle.dumps((False, RuntimeError(described)))


_calls: SubprocessWorkerCalls | None = None


def use_worker_calls(calls: SubprocessWorkerCalls | None) -> None:
    """Called once at the start of a function subprocess or a scheduled run."""
    global _calls
    _calls = calls


def worker_calls() -> SubprocessWorkerCalls | None:
    """The channel to the parent, or None in the process that owns the run tables."""
    return _calls


def run_table_calls() -> SubprocessWorkerCalls | None:
    """The channel, when this process must also hand its turn's run-table changes over.

    A scheduled run has the channel for its sub-agents, but keeps its own turn's rows.
    """
    from openprogram.runtime.execution.store.run_tables import process_only_reads_run_tables

    return _calls if process_only_reads_run_tables() else None


__all__ = [
    "SubprocessWorkerCalls", "WorkerCallServer", "WorkerUnavailable",
    "run_table_calls", "use_worker_calls", "worker_calls",
]
