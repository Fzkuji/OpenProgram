"""lsp_diagnostics — type-checker errors and warnings for one file."""
from __future__ import annotations

import os

from openprogram.functions._runtime import function
from openprogram.functions.tools.lsp.shared import (
    MAX_DIAGNOSTICS,
    prepare,
    truncate,
)

_SEVERITY = {1: "error", 2: "warning", 3: "info", 4: "hint"}


def _diagnostics_impl(file: str) -> str:
    """The tool's body, callable as plain Python (the @function wrapper
    below is the LLM-facing dispatch surface)."""
    server, uri, error = prepare(file)
    if error:
        return error
    assert server is not None

    diagnostics = server.wait_for_diagnostics(uri)
    name = os.path.basename(file)
    if not diagnostics:
        return f"No diagnostics for {name}"

    def sort_key(item: dict) -> tuple:
        start = (item.get("range") or {}).get("start") or {}
        return (start.get("line", 0), start.get("character", 0))

    lines = []
    for item in sorted(diagnostics, key=sort_key):
        start = (item.get("range") or {}).get("start") or {}
        severity = _SEVERITY.get(item.get("severity", 1), "error")
        message = " ".join((item.get("message") or "").split())
        source = item.get("source") or ""
        suffix = f" [{source}]" if source else ""
        lines.append(
            f"{start.get('line', 0) + 1}:{start.get('character', 0) + 1} "
            f"{severity}: {message}{suffix}"
        )

    header = f"{name}: {len(lines)} diagnostic{'s' if len(lines) != 1 else ''}"
    return header + "\n" + truncate(lines, MAX_DIAGNOSTICS, "diagnostics")


@function(
    name="lsp_diagnostics",
    accept_edits_safe=True,   # read-only analysis
    description=(
        "Compiler-grade errors and warnings for one file, from a language "
        "server reading the file as it currently sits on disk.\n"
        "\n"
        "- Use right after editing a file to catch type errors, undefined "
        "names and bad imports without running the test suite.\n"
        "- Python needs pyright, TypeScript/JavaScript needs "
        "typescript-language-server; when the binary is missing the tool "
        "says so and installs are one npm command.\n"
        "- Output is one `line:col severity message` per finding.\n"
        "\n"
        "Args:\n"
        "  file: absolute path to the file to check."
    ),
    toolset=["core"],
)
def lsp_diagnostics(file: str) -> str:
    """Report type-checker diagnostics for a file."""
    return _diagnostics_impl(file)
