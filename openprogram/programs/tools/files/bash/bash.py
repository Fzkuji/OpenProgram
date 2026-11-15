"""bash function — run a shell command; a long one keeps running as a session.

Single source of truth: the @function decorator builds an AgentTool from
this function's signature + docstring.
"""

from __future__ import annotations

import json
import sys

from openprogram.host.backend import get_active_backend
from openprogram.programs._host.runtime import function
from openprogram.state.worktree.context import current_worktree_path

from .prompt import DEFAULT_MAX_TIMEOUT_MS, DESCRIPTION
from openprogram.host.backend.workdir import prepare_command
from openprogram.host.sandbox import SandboxPathDenied
from openprogram.runtime.permissions import refusal


# Bash output can be huge (find /, full log dump). 7,500 weight is about
# Claude Code's 30,000-character BashTool cap in English. The agent loop
# saves the complete output to a file the result names, so the model can
# read the part it needs.
@function(
    name="bash",
    description=DESCRIPTION,
    max_result_weight=7_500,
    toolset=["core"],
    unsafe_in=["plan"],  # hidden in plan mode
    # workdir is resolved and checked in prepare_command against the ORIGINAL
    # workspace; generic parameter checks cannot resolve remote filesystem paths.
    # Commands still pass through the backend / OS sandbox.
    path_params={},
    url_params=[],
)
def bash(command: str,
        description: str | None = None,
        workdir: str | None = None,
        yield_time_ms: int | None = None,
        tty: bool = False,
        sandbox_permissions: str | None = None,
        justification: str | None = None) -> str:
    """Run a shell command via the active backend (local / docker / ssh).

    Args:
        command: The shell command to execute.
        description: Short active-voice description of what the command
            does, such as "Evaluate 100 samples"; it names the command in the
            UI while it runs (display only).
        workdir: Starting directory for this call only. Relative paths use the
            bound worktree, or the local process directory when unbound. No
            shell expansion is performed. Omit to use the backend default.
        yield_time_ms: How long to wait before returning a session ID for a
            still-running command. Commands that finish sooner return
            immediately. Defaults to 30000; effective range is 250-30000.
        tty: True runs the command in a pseudo-terminal so write_stdin can
            type into it (REPLs, prompts, programs that need a terminal).
            False or omitted uses pipes with stdin closed. Local macOS/Linux only.
        sandbox_permissions: "require_escalated" asks the user, before the
            command runs, to run it outside the sandbox (network, writes
            outside the workspace). Omit to run inside the sandbox.
        justification: With require_escalated, one sentence telling the user
            why the command needs to leave the sandbox; shown on the approval.
    """
    from openprogram.host.processes import current_owner

    if not (command or "").strip():
        return "Error: command is empty. Pass the shell command to run in command."
    try:
        current_owner()
    except RuntimeError:
        # No conversation to own a session (a script or test calling the
        # tool directly): run to completion as a plain subprocess.
        return _run_once(command, workdir)
    return _run_session(command, workdir, yield_time_ms, tty, description)


def _run_session(command, workdir, yield_time_ms, tty, description=None):
    from openprogram.models.providers.types import AgentToolResult
    from openprogram.models.providers.types import TextContent

    from . import session

    backend = get_active_backend()
    start_cwd = None
    try:
        run_command, run_cwd, start_cwd = prepare_command(
            command, workdir, backend_id=backend.backend_id,
            worktree=current_worktree_path(),
        )
        reply = session.run(command, cwd=run_cwd, run_command=run_command, tty=bool(tty),
                            yield_ms=session.clamp_exec_yield(yield_time_ms), shown_cwd=start_cwd,
                            title=description)
    except SandboxPathDenied as exc:
        raise refusal.sandbox("bash", exc.violation) from exc
    except (OSError, ValueError, RuntimeError) as exc:
        return AgentToolResult(
            content=[TextContent(text=f"Error: shell launch failed: {exc}")],
            details={"backend": backend.backend_id, "cwd": start_cwd,
                     "error": type(exc).__name__},
            is_error=True,
        )
    return session_result(reply)


def session_result(reply):
    """The tool result for one bash or write_stdin reply."""
    from openprogram.models.providers.types import AgentToolResult
    from openprogram.models.providers.types import TextContent

    from .session import sandbox_denial

    record = reply.record
    text = reply.text()
    details = {"session_id": record["id"], "running": reply.running,
               "exit_code": record["exit_code"], "status": record["status"],
               "backend": record.get("backend_id"), "cwd": record.get("cwd"),
               "tty": bool(record.get("tty"))}
    denial = sandbox_denial(reply)
    if denial:
        text += "\n" + denial
        details["sandbox"] = {"kind": "denied",
                              "backend": "seatbelt" if sys.platform == "darwin" else "bubblewrap"}
        if denial.startswith("sandbox blocked network"):
            details["sandbox"]["rule"] = "network"
    failed = not reply.running and (record["status"] != "exited" or record["exit_code"] != 0)
    return AgentToolResult(content=[TextContent(text=text)], details=details, is_error=failed)


def _run_once(command, workdir):
    # Bash output is not attributed to the current turn's exact mutation
    # journal. Only trusted file-mutating tools create recoverable receipts.
    timeout_sec = DEFAULT_MAX_TIMEOUT_MS / 1000.0

    from openprogram.models.providers.types import AgentToolResult
    from openprogram.models.providers.types import TextContent

    backend = get_active_backend()
    # A cwd override belongs to this call, never the worktree ContextVar or
    # the Python process. Consecutive and parallel calls remain independent.
    start_cwd = None
    try:
        run_command, run_cwd, start_cwd = prepare_command(
            command, workdir, backend_id=backend.backend_id,
            worktree=current_worktree_path(),
        )
        result = backend.run(run_command, timeout=timeout_sec, cwd=run_cwd)
    except SandboxPathDenied as exc:
        raise refusal.sandbox("bash", exc.violation) from exc
    except (OSError, ValueError, RuntimeError) as exc:
        return AgentToolResult(
            content=[TextContent(text=f"Error: shell launch failed: {exc}")],
            details={"backend": backend.backend_id, "cwd": start_cwd,
                     "error": type(exc).__name__},
            is_error=True,
        )
    location_key = "cwd=" if backend.backend_id == "local" else "requested_cwd="
    location = location_key + (json.dumps(start_cwd, ensure_ascii=False)
                         if start_cwd is not None else "(backend default)")

    if result.timed_out:
        text = (
            f"[timeout after {timeout_sec:.1f}s via {backend.backend_id}]\n"
            f"{location}\n"
            f"--- stdout (partial) ---\n{result.stdout}\n"
            f"--- stderr (partial) ---\n{result.stderr}"
        )
        return AgentToolResult(
            content=[TextContent(text=text)],
            details={
                "timeout": True,
                "exit_code": result.exit_code,
                "backend": backend.backend_id,
                "cwd": start_cwd,
            },
            is_error=True,
        )

    parts = [f"exit_code={result.exit_code}"]
    if backend.backend_id != "local":
        parts[0] += f" (backend={backend.backend_id})"
    parts.append(location)
    if result.stdout:
        parts.append(f"--- stdout ---\n{result.stdout.rstrip()}")
    if result.stderr:
        parts.append(f"--- stderr ---\n{result.stderr.rstrip()}")
    text = "\n".join(parts)
    if result.sandbox_error == "denied":
        from openprogram.host.sandbox import named_denial_text, network_denial_text
        text = text + "\n" + (
            network_denial_text() if result.sandbox_rule == "network"
            else named_denial_text(result.sandbox_path, result.sandbox_rule)
        )
        sandbox = {
            "kind": result.sandbox_error,
            "backend": "seatbelt" if sys.platform == "darwin"
            else "bubblewrap",
        }
        if result.sandbox_path:
            sandbox["path"] = result.sandbox_path
        if result.sandbox_rule:
            sandbox["rule"] = result.sandbox_rule
        return AgentToolResult(
            content=[TextContent(text=text)],
            details={"sandbox": sandbox, "cwd": start_cwd},
            is_error=True,
        )
    if result.exit_code != 0:
        return AgentToolResult(
            content=[TextContent(text=text)],
            details={
                "exit_code": result.exit_code,
                "backend": backend.backend_id,
                "cwd": start_cwd,
            },
            is_error=True,
        )
    return text
