"""bash function — run a shell command, return stdout/stderr/exit code.

Single source of truth: the @function decorator builds an AgentTool from
this function's signature + docstring.
"""

from __future__ import annotations

import sys

from openprogram.backend import get_active_backend
from openprogram.programs._runtime import function
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
    # Exempt: paths live inside `command`, not on the parameter surface.
    # wrap_command + the OS sandbox already inspect the argv/cwd tree.
    path_params={},
    url_params=[],
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

    from openprogram.agent.types import AgentToolResult
    from openprogram.providers.types import TextContent

    if result.timed_out:
        text = (
            f"[timeout after {timeout_sec:.1f}s via {backend.backend_id}]\n"
            f"--- stdout (partial) ---\n{result.stdout}\n"
            f"--- stderr (partial) ---\n{result.stderr}"
        )
        return AgentToolResult(
            content=[TextContent(text=text)],
            details={
                "timeout": True,
                "exit_code": result.exit_code,
                "backend": backend.backend_id,
            },
            is_error=True,
        )

    parts = [f"exit_code={result.exit_code}"]
    if backend.backend_id != "local":
        parts[0] += f" (backend={backend.backend_id})"
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
            details={"sandbox": sandbox},
            is_error=True,
        )
    if result.exit_code != 0:
        return AgentToolResult(
            content=[TextContent(text=text)],
            details={
                "exit_code": result.exit_code,
                "backend": backend.backend_id,
            },
            is_error=True,
        )
    return text
