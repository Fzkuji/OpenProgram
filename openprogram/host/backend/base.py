"""Backend ABC + shared RunResult type."""
from __future__ import annotations

import abc
import contextlib
import subprocess
import threading
import time
import weakref
from dataclasses import dataclass


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    sandbox_error: str | None = None
    sandbox_path: str | None = None
    sandbox_rule: str | None = None


def decode_maybe(x) -> str:
    """Normalize bytes/None/str into a safe str for RunResult fields."""
    if x is None:
        return ""
    if isinstance(x, bytes):
        return x.decode(errors="replace")
    return x


# Commands running now, per machine id, so a machine switched off or removed lets them finish before its
# instances go. Spawned processes count while they live.
_running: dict[str, int] = {}
_spawned: dict[str, "weakref.WeakSet[subprocess.Popen]"] = {}
_idle = threading.Condition()


@contextlib.contextmanager
def running_on(machine_id: str):
    """Count one command on ``machine_id`` while it runs; "" (this computer) is not counted."""
    if not machine_id:
        yield
        return
    with _idle:
        _running[machine_id] = _running.get(machine_id, 0) + 1
    try:
        yield
    finally:
        with _idle:
            left = _running.get(machine_id, 1) - 1
            if left:
                _running[machine_id] = left
            else:
                _running.pop(machine_id, None)
            _idle.notify_all()


def spawned_on(machine_id: str, proc: subprocess.Popen) -> subprocess.Popen:
    """Count a spawned process on ``machine_id`` until it exits."""
    if machine_id:
        with _idle:
            _spawned.setdefault(machine_id, weakref.WeakSet()).add(proc)
    return proc


def _busy(machine_id: str) -> tuple[bool, bool]:
    """Whether commands run on the machine, and whether one of them is a spawned process."""
    alive = [proc for proc in _spawned.get(machine_id, ()) if proc.poll() is None]
    if not alive:
        _spawned.pop(machine_id, None)
    return bool(_running.get(machine_id)) or bool(alive), bool(alive)


def wait_idle(machine_id: str, timeout: float) -> bool:
    """Wait until no command runs on ``machine_id``; False when ``timeout`` seconds passed first."""
    deadline = time.monotonic() + timeout
    with _idle:
        while True:
            busy, spawned = _busy(machine_id)
            if not busy:
                return True
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            # A spawned process ends without telling; look again shortly.
            _idle.wait(min(left, 0.5) if spawned else left)


class Backend(abc.ABC):
    backend_id: str = ""
    # The machine commands run on, when it is not this computer: they are counted while they run.
    machine_id: str = ""

    @abc.abstractmethod
    def run(self, command: str, timeout: float,
            cwd: str | None = None) -> RunResult:
        """Execute ``command`` and return the full result.

        Implementations MUST:
          * honour ``timeout`` (seconds); set ``timed_out=True`` instead
            of raising when it fires
          * return partial stdout/stderr even on timeout when available
          * never raise from normal execution paths — return a RunResult
            with exit_code != 0 and an informative stderr instead, so
            the calling tool doesn't surface raw exceptions to the LLM
        """

    def spawn_spec(self, command: str, cwd: str | None = None) -> dict:
        """Resolve a launch in the caller's backend and sandbox context.

        The serializable Popen arguments can be handed to a durable supervisor.
        They are not persisted, especially the environment.
        """
        raise NotImplementedError(f"backend {self.backend_id!r} does not support durable spawn")

    def spawn(self, command: str,
              cwd: str | None = None) -> subprocess.Popen:
        """Start a long-lived process; return the Popen handle.

        Used by the ``process`` tool for interactive sessions that need
        stdin/stdout streaming over time. Default implementation raises
        ``NotImplementedError``; concrete backends override.

        Pipes: stdin=PIPE, stdout=PIPE, stderr=merged (STDOUT) so the
        caller can line-iterate stdout without juggling two streams.
        """
        raise NotImplementedError(
            f"backend {self.backend_id!r} doesn't support spawn() yet"
        )
