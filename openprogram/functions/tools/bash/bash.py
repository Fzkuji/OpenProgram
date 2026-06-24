"""bash function — run a shell command, return stdout/stderr/exit code.

Single source of truth: the @function decorator builds an AgentTool from
this function's signature + docstring.
"""

from __future__ import annotations

from openprogram.backend import get_active_backend
from openprogram.functions._runtime import function
from openprogram.worktree.context import current_worktree_path

from .prompt import DEFAULT_MAX_TIMEOUT_MS, DEFAULT_TIMEOUT_MS, DESCRIPTION


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
)
def bash(command: str,
        timeout: float | None = None,
        description: str | None = None) -> str:
    """Run a shell command via the active backend (local / docker / ssh).

    Args:
        command: The shell command to execute.
        timeout: Optional timeout in milliseconds (default 30000, max 600000).
        description: Short active-voice description shown in UI (display only).
    """
    # Bash file-change tracking is handled at the _execute_tool_calls
    # level (agent_loop.py): cwd files are snapshotted before/after
    # execution, and changed files get checkpoint backup automatically.
    timeout_ms = min(timeout or DEFAULT_TIMEOUT_MS, DEFAULT_MAX_TIMEOUT_MS)
    timeout_sec = timeout_ms / 1000.0

    backend = get_active_backend()
    # When an active agent worktree is bound to the current context
    # (set by the dispatcher / task runner), tools default their cwd
    # to it. The LLM-supplied command can still ``cd ..`` out; this
    # just sets the starting directory. A new subprocess is spawned
    # per invocation, so any ``cd`` inside the command is local to
    # that one call.
    wt_cwd = current_worktree_path()
    result = backend.run(command, timeout=timeout_sec, cwd=wt_cwd)

    if result.timed_out:
        return (
            f"[timeout after {timeout_sec:.1f}s via {backend.backend_id}]\n"
            f"--- stdout (partial) ---\n{result.stdout}\n"
            f"--- stderr (partial) ---\n{result.stderr}"
        )

    parts = [f"exit_code={result.exit_code}"]
    if backend.backend_id != "local":
        parts[0] += f" (backend={backend.backend_id})"
    if result.stdout:
        parts.append(f"--- stdout ---\n{result.stdout.rstrip()}")
    if result.stderr:
        parts.append(f"--- stderr ---\n{result.stderr.rstrip()}")
    return "\n".join(parts)
