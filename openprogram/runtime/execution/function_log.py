"""One durable function call's own record, in its own file.

A durable function can run in a subprocess, which has no view of the Worker's
run tables. So what it has done is written here, by whichever process runs it:
what was admitted, the order of its nodes, and each step's start and result.
The file sits in the session's directory; the Worker reads it to learn which
steps have an unknown outcome and appends what the process must be told.

A step that started and has no result has an unknown outcome. It is reported
as an unresolved effect and never run again until someone settles it.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import os
from pathlib import Path
import threading
import time
from typing import Any, Mapping
from urllib.parse import quote

from openprogram.host._platform.filesystem import filesystem_path
from openprogram.state.store.session.session_log import (
    _next_timestamp,
    encode_record,
    read_appended,
)

FUNCTION_LOG_DIRECTORY = "functions"
_CACHED_LOGS = 128

DISPATCHED = "dispatched"
COMMITTED = "committed"


def function_log_name(execution_id: str) -> str:
    return quote(execution_id, safe="") + ".jsonl"


@dataclass(frozen=True)
class FunctionStep:
    action_id: str
    identity: Mapping[str, Any]
    input_hash: str
    code_hash: str
    attempt_id: str
    started_at: float
    status: str = DISPATCHED
    result: str | None = None
    receipt: Mapping[str, Any] | None = None
    settled_at: float | None = None


class FunctionLog:
    """Reads and appends one call's file; one process writes at a time."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._offset = 0
        self._saved: dict[tuple[str, str], Any] = {}
        self._nodes: dict[str, list[dict]] = {}
        self._steps: dict[str, FunctionStep] = {}
        self._requests: set[tuple[int, str]] = set()

    # Appending

    def _append(self, record_type: str, data: Mapping[str, Any]) -> None:
        line = encode_record(_next_timestamp(), record_type, dict(data))
        native = filesystem_path(self.path)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(native, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
            try:
                os.write(descriptor, line)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def save(self, kind: str, key: str, value: Any) -> None:
        self._append("function.save", {"kind": kind, "key": key, "value": value})

    def add_node(self, key: str, node: Mapping[str, Any]) -> None:
        self._append("function.node", {"key": key, "node": dict(node)})

    def start_step(self, *, action_id: str, identity: Mapping[str, Any], input_hash: str,
                   code_hash: str, attempt_id: str) -> None:
        self._append("function.step.started", {
            "action_id": action_id, "identity": dict(identity), "input_hash": input_hash,
            "code_hash": code_hash, "attempt_id": attempt_id, "at": time.time(),
        })

    def commit_step(self, action_id: str, result: str) -> None:
        self._append("function.step.committed", {
            "action_id": action_id, "result": result, "at": time.time(),
        })

    def resolve_step(self, action_id: str, outcome: str, receipt: Mapping[str, Any]) -> None:
        self._append("function.step.resolved", {
            "action_id": action_id, "outcome": outcome, "receipt": dict(receipt), "at": time.time(),
        })

    def request(self, command: str, generation: int) -> None:
        """Tell the process running ``generation`` to stop at its next boundary."""
        self._append("function.request", {"command": command, "generation": generation})

    # Reading

    def _refresh(self) -> None:
        with self._lock:
            try:
                if os.stat(filesystem_path(self.path)).st_size == self._offset:
                    return
            except OSError:
                return
            records, self._offset, _damaged = read_appended(self.path, self._offset)
            for record in records:
                self._apply(record.get("type"), record.get("data") or {})

    def _apply(self, record_type: Any, data: dict) -> None:
        if record_type == "function.save":
            self._saved[(data["kind"], data["key"])] = data["value"]
        elif record_type == "function.node":
            self._nodes.setdefault(data["key"], []).append(data["node"])
        elif record_type == "function.step.started":
            self._steps[data["action_id"]] = FunctionStep(
                action_id=data["action_id"], identity=data["identity"],
                input_hash=data["input_hash"], code_hash=data["code_hash"],
                attempt_id=data["attempt_id"], started_at=data["at"],
            )
        elif record_type == "function.step.committed":
            self._settle(data["action_id"], COMMITTED, data["at"], result=data["result"])
        elif record_type == "function.step.resolved":
            self._settle(data["action_id"], data["outcome"], data["at"],
                         receipt=data["receipt"], result=data["receipt"].get("result"))
        elif record_type == "function.request":
            self._requests.add((int(data["generation"]), data["command"]))

    def _settle(self, action_id: str, status: str, at: float, *,
                result: str | None = None, receipt: Mapping[str, Any] | None = None) -> None:
        step = self._steps.get(action_id)
        if step is not None:
            self._steps[action_id] = FunctionStep(**{
                **step.__dict__, "status": status, "result": result,
                "receipt": receipt, "settled_at": at,
            })

    def saved(self, kind: str, key: str) -> Any:
        with self._lock:
            self._refresh()
            return self._saved.get((kind, key))

    def nodes(self, key: str) -> list[dict]:
        with self._lock:
            self._refresh()
            return [dict(node) for node in self._nodes.get(key, ())]

    def step(self, action_id: str) -> FunctionStep | None:
        with self._lock:
            self._refresh()
            return self._steps.get(action_id)

    def unresolved(self) -> list[FunctionStep]:
        with self._lock:
            self._refresh()
            return sorted((step for step in self._steps.values() if step.status == DISPATCHED),
                          key=lambda step: step.started_at)

    def requested(self, generation: int) -> set[str]:
        with self._lock:
            self._refresh()
            return {command for owner, command in self._requests if owner == generation}

    def admitted(self) -> list[dict]:
        with self._lock:
            self._refresh()
            return [value for (kind, _key), value in self._saved.items() if kind == "function.admitted"]

    def settled_at_boundary(self, call_key: str) -> bool:
        """Admitted, and every step under this call has a known outcome."""
        with self._lock:
            self._refresh()
            if ("function.admitted", call_key) not in self._saved:
                return False
            nested = call_key + "/"
            return not any(
                str(step.identity.get("call")) == call_key
                or str(step.identity.get("call")).startswith(nested)
                for step in self.unresolved()
            )


_logs: "OrderedDict[str, FunctionLog]" = OrderedDict()
_logs_lock = threading.Lock()


def function_log_at(path: Path | str) -> FunctionLog:
    """The one reader this process keeps for ``path``."""
    key = str(path)
    with _logs_lock:
        log = _logs.get(key)
        if log is None:
            log = _logs[key] = FunctionLog(Path(path))
            while len(_logs) > _CACHED_LOGS:
                _logs.popitem(last=False)
        _logs.move_to_end(key)
        return log
