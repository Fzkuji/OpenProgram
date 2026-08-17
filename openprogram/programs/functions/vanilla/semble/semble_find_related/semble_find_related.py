"""semble_find_related — find code similar to a given file:line.

Anchors on the chunk containing the location and returns semantically
similar chunks from the same repo. Index cache lives in the family's
``shared`` module.
"""
from __future__ import annotations

import os
from typing import Optional

from openprogram.programs._runtime import function
from openprogram.programs.functions.vanilla.semble.shared import (
    _format_results,
    _get_or_build_index,
    _resolve_path,
)


_DESCRIPTION = (
    "Given a file path and line number, return chunks semantically "
    "similar to the code at that location. Useful after "
    "`semble_search` to discover related implementations or "
    "alternative call sites.\n"
    "\n"
    "- `file_path` may be absolute or relative to `path`.\n"
    "- `line` is 1-based."
)


@function(
    name="semble_find_related",
    description=_DESCRIPTION,
    max_result_chars=20_000,
    toolset=["core", "research"],
)
def semble_find_related(file_path: str,
                        line: int,
                        path: Optional[str] = None,
                        top_k: int = 5) -> str:
    """Find code semantically similar to a given file:line.

    Args:
        file_path: File to anchor on (absolute or repo-relative).
        line: Line number within the file (1-based).
        path: Repo root. Defaults to cwd.
        top_k: Max chunks (1-20). Default 5.
    """
    root = _resolve_path(path)
    if not os.path.isdir(root):
        return f"Error: path not a directory: {root}"
    top_k = max(1, min(20, int(top_k)))
    try:
        idx = _get_or_build_index(root)
    except Exception as e:  # noqa: BLE001
        return f"Error: failed to build index for {root}: {type(e).__name__}: {e}"

    abs_target = (
        file_path if os.path.isabs(file_path)
        else os.path.join(root, file_path)
    )
    try:
        rel = os.path.relpath(os.path.abspath(abs_target), root)
    except ValueError:
        return f"Error: file_path not under repo root: {file_path}"

    anchor = None
    for c in idx.chunks:
        if c.file_path == rel and c.start_line <= line <= c.end_line:
            anchor = c
            break
    if anchor is None:
        return f"Error: no chunk found at {rel}:{line}"

    try:
        results = idx.find_related(anchor, top_k=top_k)
    except Exception as e:  # noqa: BLE001
        return f"Error: find_related failed: {type(e).__name__}: {e}"
    return _format_results(results)
