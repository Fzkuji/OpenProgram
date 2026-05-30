"""read function — read a file from disk and return its contents."""

from __future__ import annotations

import os

from openprogram.functions._runtime import function
from openprogram.worktree.path_resolve import resolve_path


MAX_LINES_DEFAULT = 2000
MAX_LINE_LENGTH = 2000

_DESCRIPTION = (
    "Read a file from disk and return its contents as text, with line numbers "
    "in `cat -n` style (1-based).\n"
    "\n"
    "- Paths MUST be absolute.\n"
    "- By default reads up to 2000 lines from the top. Use `offset` and `limit` "
    "to page through larger files.\n"
    "- Individual lines longer than 2000 characters are truncated with an ellipsis.\n"
    "- Binary files are not supported — use bash if you need hex dumps."
)


@function(
    name="read",
    description=_DESCRIPTION,
    # The tool already self-bounds via offset/limit, so we don't need
    # framework persist-to-disk on top — the LLM controls page size.
    max_result_chars=200_000,
    persist_full=False,
    toolset=["core", "research"],
)
def read(file_path: str,
         offset: int = 1,
         limit: int = MAX_LINES_DEFAULT) -> str:
    """Read a file and return its contents with line numbers.

    Args:
        file_path: Absolute path of the file to read.
        offset: Line number to start reading from (1-based). Default 1.
        limit: Maximum number of lines to return. Default 2000.
    """
    # Worktree-aware resolution: relative paths bind to the active
    # worktree root when one is set; absolute paths outside the
    # worktree get a soft warning but still proceed (D6).
    resolved_path, outside_warning = resolve_path(file_path)
    file_path = resolved_path
    if not os.path.isabs(file_path):
        return f"Error: file_path must be absolute, got {file_path!r}"
    if not os.path.exists(file_path):
        return f"Error: file not found: {file_path}"
    if os.path.isdir(file_path):
        return f"Error: {file_path} is a directory, not a file"

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return f"Error reading {file_path}: {type(e).__name__}: {e}"

    # Record this file's on-disk state as the agent's read-before-edit
    # baseline (Claude-Code-style): a later edit/write validates against
    # it so the agent can't clobber a concurrent user change unseen.
    # Fingerprints the WHOLE file even on a paged read — the contract is
    # about the file changing on disk, not the page. No-op outside a turn.
    try:
        from openprogram.store import read_tracking as _rt
        _rt.mark_seen(file_path)
    except Exception:
        pass

    total = len(lines)
    start = max(1, offset) - 1
    end = min(total, start + max(1, limit))
    selected = lines[start:end]

    out_lines = []
    for i, line in enumerate(selected, start=start + 1):
        text = line.rstrip("\n")
        if len(text) > MAX_LINE_LENGTH:
            text = text[:MAX_LINE_LENGTH] + "…[truncated]"
        out_lines.append(f"{i:>6}\t{text}")

    header = f"# {file_path} (lines {start + 1}-{end} of {total})"
    if outside_warning:
        header = f"{outside_warning}\n{header}"
    if not out_lines:
        return header + "\n(empty range)"
    return header + "\n" + "\n".join(out_lines)
