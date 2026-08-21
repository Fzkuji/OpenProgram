"""process tool — manage background shell sessions started with the ``start`` action.

Separate from ``bash`` (which is synchronous and returns only when the command
exits). Use this when you need long-running servers, watchers, or any command
whose output you want to poll over time.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import threading
import time
import uuid
from typing import Any

from openprogram.programs._runtime import function


NAME = "process"

DESCRIPTION = (
    "Manage long-running background shell sessions. Pair with `bash` (which is "
    "foreground/blocking) when you need to start a dev server, poll logs, "
    "or write to a subprocess' stdin.\n"
    "\n"
    "Actions:\n"
    "  start    — launch a command in the background, returns sessionId\n"
    "  list     — show all sessions (running + exited)\n"
    "  poll     — status + exit code + new output since last poll\n"
    "  log      — full accumulated stdout/stderr\n"
    "  write    — send a line to the session's stdin\n"
    "  kill     — SIGTERM the process (waits up to 5s, then SIGKILL)\n"
    "  remove   — kill + discard the session\n"
)

SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "list", "poll", "log", "write", "kill", "remove"],
                "description": "What to do.",
            },
            "command": {
                "type": "string",
                "description": "Shell command — required for action=start.",
            },
            "session_id": {
                "type": "string",
                "description": "Target session — required for poll/log/write/kill/remove.",
            },
            "input": {
                "type": "string",
                "description": "Data to send to stdin (appended with a trailing newline) — for action=write.",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for action=start. Default: current process cwd.",
            },
        },
        "required": ["action"],
    },
}


_MAX_BUFFER_LINES = 2000
_MAX_BUFFER_BYTES = 2 * 1024 * 1024
_EXITED_TTL = 3600.0


class _Session:
    __slots__ = ("id", "command", "proc", "started_at", "buffer", "offset",
                 "_lock", "_reader", "backend_id", "truncated", "_bytes",
                 "exited_at")

    def __init__(self, command: str, cwd: str | None):
        self.id = uuid.uuid4().hex[:8]
        self.command = command
        self.buffer: list[str] = []
        self.offset = 0  # bytes of buffer already returned by poll()
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.truncated = False
        self._bytes = 0
        self.exited_at: float | None = None
        # Route through backend so `openprogram setup backend` takes
        # effect here too. LocalBackend.spawn reproduces the original
        # Popen flags; ssh / docker backends wrap appropriately.
        from openprogram.backend import get_active_backend
        self.backend_id = get_active_backend().backend_id
        self.proc = get_active_backend().spawn(command, cwd=cwd)
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _trim_locked(self) -> None:
        while (
            self.buffer
            and (
                len(self.buffer) > _MAX_BUFFER_LINES
                or self._bytes > _MAX_BUFFER_BYTES
            )
        ):
            dropped = self.buffer.pop(0)
            self._bytes -= len(dropped)
            self.offset = max(0, self.offset - len(dropped))
            self.truncated = True

    def _mark_exited(self) -> None:
        if self.exited_at is None and self.proc.poll() is not None:
            self.exited_at = time.time()

    def _drain(self) -> None:
        assert self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                with self._lock:
                    self.buffer.append(line)
                    self._bytes += len(line)
                    self._trim_locked()
        except Exception:
            pass
        self._mark_exited()

    def status(self) -> str:
        code = self.proc.poll()
        if code is None:
            return "running"
        self._mark_exited()
        return f"exited({code})"

    def full_log(self) -> str:
        with self._lock:
            return "".join(self.buffer)

    def poll_delta(self) -> str:
        with self._lock:
            full = "".join(self.buffer)
            delta = full[self.offset:]
            self.offset = len(full)
        return delta

    def write_input(self, text: str) -> None:
        if self.proc.stdin is None or self.proc.stdin.closed:
            raise RuntimeError("session stdin is closed")
        self.proc.stdin.write(text if text.endswith("\n") else text + "\n")
        self.proc.stdin.flush()

    def kill(self) -> None:
        if self.proc.poll() is not None:
            self._mark_exited()
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except ProcessLookupError:
            pass
        self._mark_exited()


_SESSIONS: dict[str, _Session] = {}
_LOCK = threading.Lock()


def _get(sid: str) -> _Session | None:
    with _LOCK:
        return _SESSIONS.get(sid)


def _remove(sid: str) -> _Session | None:
    with _LOCK:
        return _SESSIONS.pop(sid, None)


def _reap_exited(now: float | None = None) -> None:
    now = time.time() if now is None else now
    with _LOCK:
        stale = []
        for sid, sess in _SESSIONS.items():
            if sess.proc.poll() is None:
                continue
            sess._mark_exited()
            if sess.exited_at is not None and now - sess.exited_at >= _EXITED_TTL:
                stale.append(sid)
        for sid in stale:
            _SESSIONS.pop(sid, None)


def execute(
    action: str,
    command: str | None = None,
    session_id: str | None = None,
    input: str | None = None,
    cwd: str | None = None,
    **_: Any,
) -> str:
    if action == "start":
        if not command:
            return "Error: action=start requires command"
        try:
            sess = _Session(command, cwd)
        except Exception as e:
            return f"Error starting process: {type(e).__name__}: {e}"
        with _LOCK:
            _SESSIONS[sess.id] = sess
        suffix = f" backend={sess.backend_id}" if sess.backend_id != "local" else ""
        return f"started session_id={sess.id} pid={sess.proc.pid}{suffix}"

    if action == "list":
        _reap_exited()
        with _LOCK:
            sessions = list(_SESSIONS.values())
        if not sessions:
            return "(no sessions)"
        rows = []
        for s in sessions:
            flag = "  truncated" if s.truncated else ""
            rows.append(
                f"{s.id}  {s.status():<12}  pid={s.proc.pid}  "
                f"age={int(time.time() - s.started_at)}s{flag}  {s.command}"
            )
        return "\n".join(rows)

    if not session_id:
        return f"Error: action={action} requires session_id"
    if action == "poll":
        _reap_exited()
    sess = _get(session_id)
    if sess is None:
        return f"Error: no session {session_id!r}"

    if action == "poll":
        delta = sess.poll_delta()
        extra = " truncated=1" if sess.truncated else ""
        if delta:
            return f"status={sess.status()}{extra}\n--- new output ---\n{delta}"
        return f"status={sess.status()}{extra} (no new output)"
    if action == "log":
        extra = " truncated=1" if sess.truncated else ""
        return f"status={sess.status()}{extra}\n--- log ---\n{sess.full_log()}"
    if action == "write":
        if input is None:
            return "Error: action=write requires input"
        try:
            sess.write_input(input)
        except Exception as e:
            return f"Error writing to {session_id}: {type(e).__name__}: {e}"
        return f"wrote {len(input)} bytes to {session_id}"
    if action == "kill":
        try:
            sess.kill()
        except Exception as e:
            return f"Error killing {session_id}: {type(e).__name__}: {e}"
        return f"killed {session_id} (status={sess.status()})"
    if action == "remove":
        try:
            sess.kill()
        except Exception as e:
            return f"Error killing {session_id}: {type(e).__name__}: {e}"
        _remove(session_id)
        return f"removed {session_id}"

    return f"Error: unknown action {action!r}"


# Register as an AgentTool. ``execute`` stays a plain callable so any
# existing import-and-call sites keep working; the return value (an
# AgentTool) is discarded — it's already in the registry.
function(
    name=NAME,
    description=DESCRIPTION,
    parameters=SPEC["parameters"],
    toolset=['core'],
    unsafe_in=['wechat', 'telegram', 'plan'],
    # Exempt: same as bash — the command string is not a surface path.
    # Child sessions go through the backend / OS sandbox.
    path_params={},
    url_params=[],
)(execute)
