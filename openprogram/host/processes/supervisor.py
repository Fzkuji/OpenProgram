"""Detached owner of one managed process and its pipes.

Only this process sends signals, using its unreaped Popen child identity. The
worker never signals a PID loaded from SQLite. Launch data arrives through a
private inherited pipe and is not stored on disk; once the record says it
runs, one line on the inherited stdout tells the starter so.

The control loop sleeps until something wakes it, never on a tick:

* the command ending: on POSIX ``SIGCHLD`` from its group leader, written to
  the loop's wake socket by ``signal.set_wakeup_fd``; on Windows the job's
  I/O completion port, followed by one thread
  (``ProcessTreeOwner.watch_members``);
* a stop, interrupt or stdin row: its writer connects to this supervisor's
  doorbell (``channel.Doorbell``) after inserting it;
* a blocked stdin write: the pipe becoming writable (POSIX), or the writer
  thread finishing it (Windows);
* the end of a stop's five-second grace, the one deadline.

After each write to the record (output, status, an acknowledgement) the
doorbell rings every waiter.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
import queue
import select
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time

from .channel import READY_LINE, Doorbell
from .store import ProcessStore, process_identity

#: A stopped command's group has this long after SIGTERM before SIGKILL.
STOP_GRACE_SECONDS = 5.0


def wait_timeout(stopping_at: float | None, killed: bool, now: float) -> float | None:
    """How long the control loop may sleep: until a stop's grace ends, else
    until something wakes it (``None``)."""
    if stopping_at is None or killed:
        return None
    return max(0.0, stopping_at + STOP_GRACE_SECONDS - now)


class _Wake:
    """A socket pair the control loop selects on; writing to it from any
    thread, or a signal through ``set_wakeup_fd``, wakes the loop."""

    def __init__(self) -> None:
        self.read, self.write = socket.socketpair()
        self.read.setblocking(False)
        self.write.setblocking(False)

    def __call__(self) -> None:
        try:
            self.write.send(b"\0")
        except OSError:
            pass  # already woken, or closing

    def drain(self) -> None:
        try:
            while self.read.recv(4096):
                pass
        except OSError:
            pass

    def wait(self, timeout: float) -> None:
        select.select([self.read], [], [], max(0.0, timeout))
        self.drain()

    def close(self) -> None:
        self.read.close()
        self.write.close()


@dataclass
class _StdinWrite:
    command_id: str
    payload: bytes
    delivered: int = 0
    error: Exception | None = None
    done: threading.Event = field(default_factory=threading.Event)


class _WindowsStdinWriter:
    """One writer keeps a blocked Windows pipe off the control loop, and
    wakes the loop when a write finishes."""

    def __init__(self, fd: int, wake):
        self.fd = fd
        self.wake = wake
        self.jobs: queue.Queue[_StdinWrite | None] = queue.Queue()
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while (job := self.jobs.get()) is not None:
            try:
                while job.delivered < len(job.payload):
                    written = os.write(self.fd, job.payload[job.delivered:])
                    if written <= 0:
                        raise BrokenPipeError("stdin write made no progress")
                    job.delivered += written
            except Exception as exc:
                job.error = exc
            finally:
                job.done.set()
                self.wake()

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        self.jobs.put(None)
        self.thread.join(timeout=1)


def _announce_ready() -> None:
    """Tell the starter the record runs, then let go of its pipe."""
    try:
        os.write(1, READY_LINE)
    except OSError:
        pass  # the starter is gone; the record says it all
    devnull = os.open(os.devnull, os.O_WRONLY | getattr(os, "O_BINARY", 0))
    os.dup2(devnull, 1)
    os.close(devnull)


def main():
    store = ProcessStore(sys.argv[1])
    process_id = sys.argv[2]
    proc = None
    tree = None
    reader = None
    stdin_writer = None
    stop_members = None
    pending_write = None
    read_error = []
    wake = _Wake()
    bell = Doorbell()
    if os.name == "posix":
        # A child's exit interrupts nothing; it writes a byte to the wake
        # socket, which the loop selects on.
        signal.signal(signal.SIGCHLD, lambda *_: None)
        signal.set_wakeup_fd(wake.write.fileno(), warn_on_full_buffer=False)
    try:
        launch = json.load(sys.stdin)
        sys.stdin.close()
        launch.pop("sandboxed", None)
        expected_scope = launch.pop("_supervisor_scope", None)
        if expected_scope is not None:
            cgroups = Path("/proc/self/cgroup").read_text(encoding="utf-8")
            if not any(expected_scope in line.split(":", 2)[-1].split("/") for line in cgroups.splitlines()):
                raise RuntimeError("managed supervisor is not in its independent systemd scope")
        # The command gets its own process group; stopping the managed shell
        # also stops its ordinary descendants, without signalling the worker.
        if os.name == "posix":
            proc = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name("group_guard.py"))],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, bufsize=0, start_new_session=True,
            )
            proc.stdin.write((json.dumps(launch) + "\n").encode("utf-8"))
            proc.stdin.flush()
        else:
            from openprogram.host._platform.process_ownership import ProcessTreeOwner
            launch.pop("_tty", None)
            tree = ProcessTreeOwner()
            proc = tree.popen(**launch, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, bufsize=0)
            stop_members = tree.watch_members(wake)
        store.update(process_id, status="running", pid=proc.pid,
                     pid_identity=process_identity(proc.pid), supervisor_pid=os.getpid(),
                     supervisor_identity=process_identity(os.getpid()),
                     notify_port=bell.port, notify_token=bell.token)
        _announce_ready()

        def drain():
            try:
                while chunk := os.read(proc.stdout.fileno(), 8192):
                    store.append_output(process_id, chunk)
                    bell.ring()
            except Exception as exc:
                read_error.append(type(exc).__name__)

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        stopping_at = None
        killed = False
        write_offsets = {}
        if os.name != "posix":
            stdin_writer = _WindowsStdinWriter(proc.stdin.fileno(), wake)
        else:
            os.set_blocking(proc.stdin.fileno(), False)

        def members_alive():
            return tree.active_process_count() > 0 if tree is not None else proc.poll() is None

        def apply_commands():
            """Carry out every pending command; whether any was settled or
            the status changed, and whether a POSIX stdin write waits for
            room in the pipe."""
            nonlocal stopping_at, pending_write
            acted = False
            write_blocked = False
            for command in store.commands(process_id):
                try:
                    if command["kind"] == "stop":
                        if stopping_at is None and members_alive():
                            if os.name == "posix":
                                os.killpg(proc.pid, signal.SIGTERM)
                            else:
                                tree.terminate_members()
                            stopping_at = time.monotonic()
                            store.update(process_id, status="stopping")
                    elif command["kind"] == "interrupt":
                        # Ctrl-C for a command without a terminal: SIGINT to its
                        # group. A terminal's Ctrl-C arrives as written input.
                        if stopping_at is None and members_alive():
                            if os.name == "posix":
                                os.killpg(proc.pid, signal.SIGINT)
                            else:
                                tree.terminate_members()
                    else:
                        if stdin_writer is not None and pending_write is not None and pending_write.command_id == command["id"]:
                            if not pending_write.done.is_set():
                                write_blocked = True
                                continue
                            completed = pending_write
                            pending_write = None
                            write_offsets[command["id"]] = completed.delivered
                            if completed.error is not None:
                                raise completed.error
                            write_offsets.pop(command["id"], None)
                            store.acknowledge(command["id"])
                            acted = True
                            continue
                        if stopping_at is not None:
                            raise RuntimeError("process is stopping")
                        if write_blocked or pending_write is not None:
                            continue
                        # Raw bytes, as typed: a line needs its own "\n" or "\r".
                        payload = (command["input"] or "").encode("utf-8")
                        if stdin_writer is not None:
                            pending_write = _StdinWrite(command["id"], payload)
                            stdin_writer.jobs.put(pending_write)
                            write_blocked = True
                            continue
                        # Keep offsets until the full command is delivered;
                        # retrying the drain never repeats already written bytes.
                        offset = write_offsets.get(command["id"], 0)
                        try:
                            written = os.write(proc.stdin.fileno(), payload[offset:])
                        except BlockingIOError:
                            write_blocked = True
                            continue
                        write_offsets[command["id"]] = offset + written
                        if offset + written < len(payload):
                            write_blocked = True
                            continue
                    write_offsets.pop(command["id"], None)
                    store.acknowledge(command["id"])
                    acted = True
                except Exception as exc:
                    sent = write_offsets.pop(command["id"], 0)
                    store.acknowledge(command["id"], f"{type(exc).__name__}; delivered_bytes={sent}")
                    acted = True
            return acted, write_blocked and stdin_writer is None

        selector = selectors.DefaultSelector()
        selector.register(wake.read, selectors.EVENT_READ)
        bell.register(selector)
        watching_stdin = False
        while members_alive():
            acted, write_blocked = apply_commands()
            if acted:
                bell.ring()
            if (stopping_at is not None and not killed
                    and time.monotonic() - stopping_at >= STOP_GRACE_SECONDS and members_alive()):
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    tree.terminate_members()
                killed = True
            if write_blocked != watching_stdin:
                if write_blocked:
                    selector.register(proc.stdin, selectors.EVENT_WRITE)
                else:
                    selector.unregister(proc.stdin)
                watching_stdin = write_blocked
            for key, _events in selector.select(wait_timeout(stopping_at, killed, time.monotonic())):
                if key.fileobj is wake.read:
                    wake.drain()
                elif bell.owns(key.fileobj):
                    bell.handle(key.fileobj)
                # A writable stdin needs nothing here: the next pass writes.
        code = proc.wait()
        if stdin_writer is not None:
            # Tree termination closes the pipe's readers. Never wait forever
            # for a Windows write if a handle remains open unexpectedly.
            stdin_writer.close()
        if tree is not None:
            tree.release()  # ActiveProcesses reached zero; no descendants are detached.
            tree = None
        reader.join(timeout=2)
        # A descendant retaining the pipe must not prevent terminal state;
        # truncation explicitly distinguishes this from complete output.
        if reader.is_alive() or read_error:
            store.update(process_id, truncated=True)
        store.update(process_id, status="stopped" if stopping_at is not None else "exited",
                     exit_code=code, ended_at=time.time())
        for command in store.commands(process_id):
            if pending_write is not None and command["id"] == pending_write.command_id:
                complete = pending_write.done.is_set() and pending_write.error is None and pending_write.delivered == len(pending_write.payload)
                store.acknowledge(command["id"], None if complete else f"process exited; delivered_bytes={pending_write.delivered}")
            else:
                store.acknowledge(command["id"], None if command["kind"] == "stop" else "process exited")
        bell.ring()
    except BaseException as exc:
        confirmed_exit = proc is None
        if tree is not None and proc is not None:
            try:
                tree.terminate_members()
                if stop_members is None:
                    stop_members = tree.watch_members(wake)
                deadline = time.monotonic() + STOP_GRACE_SECONDS
                while tree.active_process_count() and time.monotonic() < deadline:
                    wake.wait(deadline - time.monotonic())
                confirmed_exit = tree.active_process_count() == 0
                if confirmed_exit:
                    proc.wait(timeout=1)
                    tree.release()
            except Exception:
                confirmed_exit = False
        elif proc is not None:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            confirmed_exit = True
        store.append_output(process_id, f"\n[supervisor failed: {type(exc).__name__}]\n".encode())
        store.update(process_id, status="failed" if confirmed_exit else "unknown",
                     ended_at=time.time() if confirmed_exit else None,
                     lost_at=None if confirmed_exit else time.time(),
                     exit_code=proc.returncode if proc else None)
        bell.ring()
    finally:
        if stop_members is not None:
            stop_members()
        if stdin_writer is not None:
            stdin_writer.close()
        if proc is not None:
            # Closing a descriptor under a still-blocked Windows write can
            # itself block. Process teardown owns that final exceptional case.
            if proc.stdin and (stdin_writer is None or not stdin_writer.thread.is_alive()):
                proc.stdin.close()
            if proc.stdout:
                proc.stdout.close()
        # Waiters read the connection's end as the supervisor's.
        bell.close()
        wake.close()


if __name__ == "__main__":
    main()
