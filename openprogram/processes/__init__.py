"""Persistent managed background processes, scoped by trusted runtime identity."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

from .store import ACTIVE, ProcessStore, public_record


def current_owner():
    from openprogram.agent.run_control import get_current_execution_id, get_current_session_id
    from openprogram.agentic_programming.function import current_call_id
    session_id = get_current_session_id()
    execution_id = get_current_execution_id()
    if not session_id:
        raise RuntimeError("managed processes require a trusted runtime session")
    if execution_id:
        from openprogram.execution import default_store
        execution = default_store().get_execution(execution_id)
        if execution is None or execution.session_id != session_id:
            raise RuntimeError("managed process execution ownership is invalid")
    return session_id, execution_id, current_call_id() or None


def _supervisor_command(store, process_id, launch):
    command = [sys.executable, "-m", "openprogram.processes.supervisor", str(store.path), process_id]
    if not sys.platform.startswith("linux"):
        return command
    # setsid does not move a child out of a systemd service's cgroup. Keep
    # worker KillMode untouched and assign only this supervisor to a new scope.
    cgroups = Path("/proc/self/cgroup").read_text()
    services = [part for line in cgroups.splitlines() for part in line.split(":", 2)[-1].split("/")
                if part.endswith(".service") and not part.startswith("user@")]
    if not services:
        return command
    runner = shutil.which("systemd-run")
    if runner is None:
        raise RuntimeError("durable processes from a systemd service require systemd-run --user")
    scope = f"openprogram-managed-{process_id}.scope"
    launch["_supervisor_scope"] = scope
    options = [runner, "--user", "--scope", "--quiet", f"--unit={scope}", "--slice=app.slice"]
    # Scope argument expansion was introduced in systemd 254. Older versions
    # already pass literal arguments and do not recognize this switch.
    version = subprocess.run([runner, "--version"], capture_output=True, text=True,
                             check=True, timeout=3)
    if int(version.stdout.split()[1]) >= 254:
        options.append("--expand-environment=no")
    return [*options, "--", *command]


def start(command, cwd=None, *, store=None):
    from openprogram.backend import get_active_backend
    session_id, execution_id, tool_call_id = current_owner()
    if not isinstance(command, str) or not command.strip():
        raise ValueError("start requires a shell command")
    backend = get_active_backend()
    # Resolve sandbox policy before leaving the trusted caller context.
    launch = backend.spawn_spec(command, cwd=cwd)
    store = store or ProcessStore()
    record = store.create(session_id=session_id, execution_id=execution_id,
                          tool_call_id=tool_call_id, command=command,
                          cwd=cwd or (os.getcwd() if backend.backend_id == "local" else None), backend_id=backend.backend_id)
    options = {"start_new_session": True} if os.name == "posix" else {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
    }
    supervisor = None
    try:
        supervisor = subprocess.Popen(
            _supervisor_command(store, record["id"], launch),
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **options,
        )
        supervisor.stdin.write(json.dumps(launch).encode("utf-8"))
        supervisor.stdin.close()
        # Reap if the worker remains alive; the supervisor's lifetime and
        # output are independent of this optional local waiter.
        threading.Thread(target=supervisor.wait, daemon=True).start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            record = store.get(record["id"])
            if record["status"] != "starting":
                break
            if supervisor.poll() is not None:
                store.update(record["id"], status="unknown", ended_at=None)
                break
            time.sleep(0.05)
    except Exception:
        # Once a supervisor was created, its child may exist even if the
        # launch acknowledgement was lost. Only failed Popen proves no child.
        store.update(record["id"], status="failed" if supervisor is None else "unknown",
                     ended_at=time.time() if supervisor is None else None)
        raise
    return store.get(record["id"])


def control(store, process_id, kind, text=None, *, wait=True):
    command_id = store.command(process_id, kind, text)
    if command_id is None:
        return store.get(process_id)
    if wait:
        deadline = time.monotonic() + (8 if kind == "stop" else 3)
        while time.monotonic() < deadline:
            result = store.command_result(command_id)
            record = store.get(process_id)
            if result and result["status"] == "failed":
                raise RuntimeError(result["error"] or "process control failed")
            if result and result["status"] == "applied" and (kind != "stop" or record["status"] not in ACTIVE):
                return record
            time.sleep(0.05)
        raise RuntimeError("process control is pending; inspect status before retrying")
    return store.get(process_id)


def scoped_records(store, session_id):
    from openprogram.execution import default_store
    from openprogram.execution.conversation_scope import conversation_executions
    executions = conversation_executions(default_store(), session_id)
    return store.list(session_id, [item.execution_id for item in executions])
