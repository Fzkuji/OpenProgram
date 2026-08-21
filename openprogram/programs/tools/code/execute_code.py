"""execute_code tool — run a Python snippet in a fresh subprocess.

Runs the code in a separate Python subprocess so the agent can perform
scratch computation and data processing without shell-quoting requirements.
Captures stdout + stderr + exit code + elapsed seconds.

Why a subprocess (not exec() in-process):
  * prints don't leak into the parent's streams
  * faulty snippets can't trash the agent's globals / threads
  * native crashes (segfault) don't take the agent down
  * timeouts are enforceable

Security boundary:
  * the local backend uses the configured host-native OpenProgram sandbox
    through ``LocalBackend.run``
  * Docker and SSH backends execute inside their configured external boundary

Inspired by hermes-agent's ``code_execution_tool`` but trimmed:
no Modal / no Docker integration, just local Python. Users who want
those can swap the subprocess call for their own runner.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Any

from openprogram.programs._helpers import read_int_param, read_string_param
from openprogram.programs._runtime import ToolReturn, function


NAME = "execute_code"

DEFAULT_TIMEOUT = 60.0
MAX_TIMEOUT = 600.0
MAX_OUTPUT_BYTES = 256 * 1024  # captured streams are truncated past this

DESCRIPTION = (
    "Run a Python snippet in a fresh subprocess and return stdout + "
    "stderr + exit code + elapsed time. Isolated from the agent's "
    "own process. The local backend applies the configured OpenProgram "
    "sandbox; Docker and SSH use their configured execution boundary. "
    "Use this for data wrangling, quick maths, "
    "library probes, plotting to disk — prefer bash for shell commands."
)


SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source to run."},
            "timeout": {
                "type": "number",
                "description": f"Seconds (default {DEFAULT_TIMEOUT}, max {MAX_TIMEOUT}).",
            },
            "cwd": {
                "type": "string",
                "description": "Absolute directory to run in. Default: agent's cwd.",
            },
            "python": {
                "type": "string",
                "description": "Override the Python interpreter path. Default: sys.executable.",
            },
        },
        "required": ["code"],
    },
}


def _truncate(stream: bytes) -> tuple[str, bool]:
    if len(stream) <= MAX_OUTPUT_BYTES:
        return stream.decode("utf-8", errors="replace"), False
    return stream[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"), True


def execute(
    code: str | None = None,
    timeout: float | None = None,
    cwd: str | None = None,
    python: str | None = None,
    **kw: Any,
) -> str:
    code = code or read_string_param(kw, "code", "source", "script")
    timeout = float(
        read_int_param(kw, "timeout") or (timeout if timeout is not None else DEFAULT_TIMEOUT)
    )
    cwd = cwd or read_string_param(kw, "cwd", "working_dir")
    python = python or read_string_param(kw, "python", "interpreter") or sys.executable

    if not code:
        return "Error: `code` is required."
    timeout = max(1.0, min(timeout, MAX_TIMEOUT))
    if cwd and not os.path.isabs(cwd):
        return f"Error: cwd must be absolute, got {cwd!r}."
    if cwd and not os.path.isdir(cwd):
        return f"Error: cwd does not exist: {cwd}"

    # Pick execution path based on the active backend. Local gets the
    # tempfile treatment so stack traces name a real filename (``-c``
    # / ``<stdin>`` frames read as <string>); remote backends fall
    # back to ``python -`` + stdin since the tempfile lives on the
    # host filesystem and isn't reachable from docker/ssh.
    from openprogram.backend import get_active_backend, LocalBackend

    backend = get_active_backend()
    started = time.time()
    sandbox_error = None

    if isinstance(backend, LocalBackend):
        # The script has to live where the sandboxed child can read it.
        # The host TMPDIR is not that place: bubblewrap mounts a fresh
        # tmpfs over /tmp, so a NamedTemporaryFile written here is simply
        # absent inside the sandbox ("can't open file /tmp/tmpXXX.py").
        # The execution directory is always a writable root, on both
        # backends, so the script goes there.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", prefix=".openprogram-execute-",
            dir=cwd or os.getcwd(), delete=False, encoding="utf-8",
        ) as f:
            f.write(code)
            script_path = f.name
        try:
            completed = backend.run(
                f"{shlex.quote(python)} {shlex.quote(script_path)}",
                timeout=timeout,
                cwd=cwd,
            )
            if completed.timed_out:
                elapsed = time.time() - started
                return (
                    f"Error: timed out after {timeout:.1f}s "
                    f"(elapsed {elapsed:.1f}s)\n\n"
                    f"## stdout (partial)\n{completed.stdout[:4000]}\n\n"
                    f"## stderr (partial)\n{completed.stderr[:4000]}"
                )
            return_code = completed.exit_code
            sandbox_error = completed.sandbox_error
            stdout_b = completed.stdout.encode("utf-8")
            stderr_b = completed.stderr.encode("utf-8")
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass
    else:
        # Non-local: spawn python reading from stdin, pipe code in.
        # backend.spawn merges stderr into stdout (see Backend contract),
        # so stderr_b ends up empty; stack traces still appear in stdout
        # which is fine for the combined display below.
        shell_cmd = f"{shlex.quote(python)} -"
        if cwd:
            shell_cmd = f"cd {shlex.quote(cwd)} && {shell_cmd}"
        proc = backend.spawn(shell_cmd)
        try:
            stdout_text, _ = proc.communicate(input=code, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            try:
                proc.kill()
            except Exception:
                pass
            partial = (e.stdout or "") if isinstance(e.stdout, str) \
                      else (e.stdout or b"").decode("utf-8", errors="replace")
            elapsed = time.time() - started
            return (
                f"Error: timed out after {timeout:.1f}s "
                f"(elapsed {elapsed:.1f}s) via {backend.backend_id}\n\n"
                f"## stdout (partial)\n{partial[:4000]}"
            )
        return_code = proc.returncode
        stdout_b = stdout_text.encode("utf-8") if isinstance(stdout_text, str) \
                   else (stdout_text or b"")
        stderr_b = b""

    elapsed = time.time() - started
    out_text, out_truncated = _truncate(stdout_b)
    err_text, err_truncated = _truncate(stderr_b)
    suffix = f" backend={backend.backend_id}" if backend.backend_id != "local" else ""
    parts = [
        f"# execute_code exit={return_code} elapsed={elapsed:.2f}s{suffix}",
        "",
        "## stdout" + (" (truncated)" if out_truncated else ""),
        out_text or "(empty)",
    ]
    if stderr_b:
        parts += [
            "",
            "## stderr" + (" (truncated)" if err_truncated else ""),
            err_text or "(empty)",
        ]
    text = "\n".join(parts)
    if sandbox_error == "denied":
        return ToolReturn(
            text=text,
            is_error=True,
            json_data={
                "sandbox": {
                    "kind": sandbox_error,
                    "backend": "seatbelt" if sys.platform == "darwin"
                    else "bubblewrap",
                }
            },
        )
    return text



# Register as an AgentTool. ``execute`` stays a plain callable so any
# existing import-and-call sites keep working; the return value (an
# AgentTool) is discarded — it's already in the registry.
function(
    name=NAME,
    description=DESCRIPTION,
    parameters=SPEC["parameters"],
    toolset=['core'],
    unsafe_in=['wechat', 'telegram', 'plan'],
)(execute)

__all__ = ["NAME", "SPEC", "execute", "DESCRIPTION"]
