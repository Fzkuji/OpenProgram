"""Detached owner of one managed process and its pipes.

Only this process sends signals, using its unreaped Popen child identity. The
worker never signals a PID loaded from SQLite. Launch data arrives through a
private inherited pipe and is not stored on disk.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from .store import ProcessStore, process_identity


def main():
    store = ProcessStore(sys.argv[1])
    process_id = sys.argv[2]
    proc = None
    tree = None
    reader = None
    read_error = []
    try:
        launch = json.load(sys.stdin)
        sys.stdin.close()
        launch.pop("sandboxed", None)
        expected_scope = launch.pop("_supervisor_scope", None)
        if expected_scope is not None:
            cgroups = Path("/proc/self/cgroup").read_text()
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
            from openprogram._compat import ProcessTreeOwner
            tree = ProcessTreeOwner()
            proc = tree.popen(**launch, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, bufsize=0)
        store.update(process_id, status="running", pid=proc.pid,
                     pid_identity=process_identity(proc.pid), supervisor_pid=os.getpid(),
                     supervisor_identity=process_identity(os.getpid()), heartbeat=time.time())

        def drain():
            try:
                while chunk := os.read(proc.stdout.fileno(), 8192):
                    store.append_output(process_id, chunk)
            except Exception as exc:
                read_error.append(type(exc).__name__)

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        stopping_at = None
        last_heartbeat = 0
        write_offsets = {}
        def members_alive():
            return tree.active_process_count() > 0 if tree is not None else proc.poll() is None

        while members_alive():
            now = time.time()
            if now - last_heartbeat >= 1:
                store.update(process_id, heartbeat=now)
                last_heartbeat = now
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
                    else:
                        if stopping_at is not None:
                            raise RuntimeError("process is stopping")
                        if write_blocked:
                            continue
                        text = command["input"] or ""
                        payload = (text if text.endswith("\n") else text + "\n").encode("utf-8")
                        # Keep offsets until the full command is delivered;
                        # retrying the drain never repeats already written bytes.
                        if os.name == "posix":
                            os.set_blocking(proc.stdin.fileno(), False)
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
                except Exception as exc:
                    sent = write_offsets.pop(command["id"], 0)
                    store.acknowledge(command["id"], f"{type(exc).__name__}; delivered_bytes={sent}")
            if stopping_at is not None and time.monotonic() - stopping_at >= 5 and members_alive():
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    tree.terminate_members()
            time.sleep(0.05)
        code = proc.wait()
        if tree is not None:
            tree.release()  # ActiveProcesses reached zero; no descendants are detached.
            tree = None
        reader.join(timeout=2)
        # A descendant retaining the pipe must not prevent terminal state;
        # truncation explicitly distinguishes this from complete output.
        if reader.is_alive() or read_error:
            store.update(process_id, truncated=True)
        store.update(process_id, status="exited", exit_code=code, ended_at=time.time(), heartbeat=time.time())
        for command in store.commands(process_id):
            store.acknowledge(command["id"], None if command["kind"] == "stop" else "process exited")
    except BaseException as exc:
        confirmed_exit = proc is None
        if tree is not None and proc is not None:
            try:
                tree.terminate_members()
                deadline = time.monotonic() + 5
                while tree.active_process_count() and time.monotonic() < deadline:
                    time.sleep(0.05)
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
                     exit_code=proc.returncode if proc else None, heartbeat=time.time())
    finally:
        if proc is not None:
            if proc.stdin:
                proc.stdin.close()
            if proc.stdout:
                proc.stdout.close()


if __name__ == "__main__":
    main()
