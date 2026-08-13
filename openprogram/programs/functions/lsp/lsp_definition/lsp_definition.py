"""lsp_definition — where the symbol at a position is actually defined."""
from __future__ import annotations

from openprogram.programs._runtime import function
from openprogram.programs.functions.lsp.shared import (
    MAX_LOCATIONS,
    format_location,
    position,
    prepare,
    read_line,
    truncate,
)


def _definition_impl(file: str, line: int, column: int) -> str:
    """The tool's body, callable as plain Python (the @function wrapper
    below is the LLM-facing dispatch surface)."""
    server, uri, error = prepare(file)
    if error:
        return error
    assert server is not None

    try:
        result = server.request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": position(line, column),
        })
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"

    if isinstance(result, dict):
        locations = [result]
    else:
        locations = result or []
    if not locations:
        return f"No definition found for the symbol at {file}:{line}:{column}"

    root = server.workspace
    entries = []
    for location in locations:
        text = read_line(location)
        suffix = f"  {text}" if text else ""
        entries.append(f"{format_location(location, root)}{suffix}")

    return truncate(entries, MAX_LOCATIONS, "definitions")


@function(
    name="lsp_definition",
    accept_edits_safe=True,   # read-only analysis
    description=(
        "The true definition site of the symbol at a position, across "
        "files and installed packages.\n"
        "\n"
        "- Follows imports, re-exports and aliases to the real "
        "declaration, including into site-packages and node_modules.\n"
        "- `line` and `column` are 1-based and must point at the symbol "
        "itself.\n"
        "- Output is one `path:line:col  source text` per definition; "
        "more than one appears for overloads and union types.\n"
        "\n"
        "Args:\n"
        "  file: absolute path to the file containing the symbol.\n"
        "  line: 1-based line number of the symbol.\n"
        "  column: 1-based column of the symbol's first character."
    ),
    toolset=["core"],
)
def lsp_definition(file: str, line: int, column: int) -> str:
    """Locate the definition of the symbol at file:line:column."""
    return _definition_impl(file, line, column)
