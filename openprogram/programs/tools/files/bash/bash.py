"""bash function — run a shell command, return stdout/stderr/exit code.

Single source of truth: the @function decorator builds an AgentTool from
this function's signature + docstring.
"""

from __future__ import annotations

import json
import sys

from openprogram.backend import get_active_backend
from openprogram.programs._runtime import function
from openprogram.worktree.context import current_worktree_path

from .prompt import DEFAULT_MAX_TIMEOUT_MS, DEFAULT_TIMEOUT_MS, DESCRIPTION
from .workdir import prepare_command


# Bash output can be huge (find /, full log dump). 30K matches Claude
# Code's BashTool default. persist_full=True saves the complete output
# to disk so the LLM can re-read with the read tool when the truncated
# view doesn't suffice.
@function(
    name="bash",
    description=DESCRIPTION,
    max_result_chars=30_000,
    persist_full=True,
    toolset=["core"],
    unsafe_in=["wechat", "telegram", "plan"],  # destructive in public channels; hidden in plan mode
    # workdir is resolved and checked in prepare_command against the ORIGINAL
    # workspace; generic parameter checks cannot resolve remote filesystem paths.
    # Commands still pass through the backend / OS sandbox.
    path_params={},
    url_params=[],
)
def bash(command: str,
        timeout: float | None = None,
        description: str | None = None,
        workdir: str | None = None) -> str:
    """Run a shell command via the active backend (local / docker / ssh).

    Args:
        command: The shell command to execute.
        timeout: Optional timeout in milliseconds (default 120000, max 600000).
        description: Short active-voice description shown in UI (display only).
        workdir: Starting directory for this call only. Relative paths use the
            bound worktree, or the local process directory when unbound. No
            shell expansion is performed. Omit to use the backend default.
    """
    # Bash output is not attributed to the current turn's exact mutation
    # journal. Only trusted file-mutating tools create recoverable receipts.
    timeout_ms = min(timeout or DEFAULT_TIMEOUT_MS, DEFAULT_MAX_TIMEOUT_MS)
    timeout_sec = timeout_ms / 1000.0

    from openprogram.agent.types import AgentToolResult
    from openprogram.providers.types import TextContent

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
        from openprogram.sandbox import named_denial_text
        text = text + "\n" + named_denial_text(
            result.sandbox_path, result.sandbox_rule,
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
