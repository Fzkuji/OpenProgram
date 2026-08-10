"""lsp_references — every real use of the symbol at a position."""
from __future__ import annotations

from openprogram.functions._runtime import function
from openprogram.functions.tools.lsp.shared import (
    MAX_LOCATIONS,
    format_location,
    position,
    prepare,
    read_line,
    truncate,
)


def _references_impl(file: str, line: int, column: int) -> str:
    """The tool's body, callable as plain Python (the @function wrapper
    below is the LLM-facing dispatch surface)."""
    server, uri, error = prepare(file)
    if error:
        return error
    assert server is not None

    try:
        result = server.request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": position(line, column),
            "context": {"includeDeclaration": True},
        })
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"

    locations = result or []
    if not locations:
        return f"No references found for the symbol at {file}:{line}:{column}"

    root = server.workspace
    entries = []
    for location in locations:
        text = read_line(location)
        suffix = f"  {text}" if text else ""
        entries.append(f"{format_location(location, root)}{suffix}")

    header = f"{len(entries)} reference{'s' if len(entries) != 1 else ''}"
    return header + "\n" + truncate(entries, MAX_LOCATIONS, "references")


@function(
    name="lsp_references",
    accept_edits_safe=True,   # read-only analysis
    description=(
        "Every use of the symbol at a position, resolved by a language "
        "server rather than by text matching.\n"
        "\n"
        "- Finds call sites grep misses (aliases, re-exports, method "
        "dispatch) and skips the same-named strings and comments grep "
        "would report.\n"
        "- `line` and `column` are 1-based and must point at the symbol "
        "itself — the name, not the line it sits on.\n"
        "- Output is one `path:line:col  source text` per use, paths "
        "relative to the workspace root.\n"
        "\n"
        "Args:\n"
        "  file: absolute path to the file containing the symbol.\n"
        "  line: 1-based line number of the symbol.\n"
        "  column: 1-based column of the symbol's first character."
    ),
    toolset=["core"],
)
def lsp_references(file: str, line: int, column: int) -> str:
    """List every reference to the symbol at file:line:column."""
    return _references_impl(file, line, column)
