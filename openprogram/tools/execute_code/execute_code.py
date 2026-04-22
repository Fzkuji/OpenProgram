"""execute_code tool — run a Python snippet in a fresh subprocess.

Runs the code via ``python -c`` in an isolated subprocess so the agent
can do scratch computation / data munging without bash-quoting hell.
Captures stdout + stderr + exit code + elapsed seconds.

Why a subprocess (not exec() in-process):
  * prints don't leak into the parent's streams
  * faulty snippets can't trash the agent's globals / threads
  * native crashes (segfault) don't take the agent down
  * timeouts are enforceable

Why not a proper sandbox (docker, firejail, nsjail):
  * out of scope here; the tool is a conveniency for trusted agents
    running locally, not a hostile-code firewall. If you're exposing
    this to an untrusted LLM, wrap the whole runtime in your own
    container — not this tool's job.

Inspired by hermes-agent's ``code_execution_tool`` but trimmed:
no Modal / no Docker integration, just local Python. Users who want
those can swap the subprocess call for their own runner.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from typing import Any

from .._helpers import read_int_param, read_string_param


NAME = "execute_code"

DEFAULT_TIMEOUT = 60.0
MAX_TIMEOUT = 600.0
MAX_OUTPUT_BYTES = 256 * 1024  # captured streams are truncated past this

DESCRIPTION = (
    "Run a Python snippet in a fresh subprocess and return stdout + "
    "stderr + exit code + elapsed time. Isolated from the agent's "
    "own process. Not a security sandbox — runs with the same privileges "
    "as OpenProgram itself. Use this for data wrangling, quick maths, "
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

    # Write to a temp file instead of `-c` to avoid argv size limits +
    # make stack traces readable (``-c`` frames show as ``<string>``).
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        script_path = f.name

    started = time.time()
    try:
        try:
            completed = subprocess.run(
                [python, script_path],
                cwd=cwd or None,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            out_text = (e.stdout or b"").decode("utf-8", errors="replace")
            err_text = (e.stderr or b"").decode("utf-8", errors="replace")
            elapsed = time.time() - started
            return (
                f"Error: timed out after {timeout:.1f}s (elapsed {elapsed:.1f}s)\n\n"
                f"## stdout (partial)\n{out_text[:4000]}\n\n"
                f"## stderr (partial)\n{err_text[:4000]}"
            )
        except FileNotFoundError:
            return f"Error: python interpreter not found at {python!r}"
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass

    elapsed = time.time() - started
    out_text, out_truncated = _truncate(completed.stdout)
    err_text, err_truncated = _truncate(completed.stderr)
    parts = [
        f"# execute_code exit={completed.returncode} elapsed={elapsed:.2f}s",
        "",
        "## stdout" + (" (truncated)" if out_truncated else ""),
        out_text or "(empty)",
        "",
        "## stderr" + (" (truncated)" if err_truncated else ""),
        err_text or "(empty)",
    ]
    return "\n".join(parts)


__all__ = ["NAME", "SPEC", "execute", "DESCRIPTION"]
