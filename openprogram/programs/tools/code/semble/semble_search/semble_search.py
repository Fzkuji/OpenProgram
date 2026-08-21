"""semble_search — semantic + lexical code search.

Natural-language or code query → ranked code chunks (not whole files),
so the LLM gets the relevant region without paying for surrounding
noise. Index cache lives in the family's ``shared`` module.
"""
from __future__ import annotations

import os
from typing import Optional

from openprogram.programs._runtime import function
from openprogram.programs.tools.code.semble.shared import (
    _format_results,
    _get_or_build_index,
    _resolve_path,
)


_DESCRIPTION = (
    "Semantic + lexical code search. Returns code chunks (not whole "
    "files) ranked by relevance to a natural-language or code query.\n"
    "\n"
    "- `query` can be natural language ('how does login work') or an "
    "identifier / code snippet ('save_pretrained').\n"
    "- `path` defaults to cwd; the index is built lazily on first call "
    "and reused across calls for the worker's lifetime.\n"
    "- Honors `.gitignore` (and `.sembleignore` if present) when "
    "selecting files to index.\n"
    "- Prefer this for concept/intent queries. Use `grep` for exact-"
    "string searches (constants, env vars, error messages). Use "
    "`glob` to find files by name — semble does not search by filename."
)


@function(
    name="semble_search",
    description=_DESCRIPTION,
    max_result_chars=20_000,
    toolset=["core", "research"],
)
def semble_search(query: str,
                  path: Optional[str] = None,
                  top_k: int = 5) -> str:
    """Semantic + lexical code search.

    Args:
        query: Natural-language description or code / identifier snippet.
        path: Repo root to search. Defaults to cwd.
        top_k: Max number of chunks to return (1-20). Default 5.
    """
    root = _resolve_path(path)
    if not os.path.isdir(root):
        return f"Error: path not a directory: {root}"
    top_k = max(1, min(20, int(top_k)))
    try:
        idx = _get_or_build_index(root)
    except Exception as e:  # noqa: BLE001
        return f"Error: failed to build index for {root}: {type(e).__name__}: {e}"
    try:
        results = idx.search(query, top_k=top_k)
    except Exception as e:  # noqa: BLE001
        return f"Error: search failed: {type(e).__name__}: {e}"
    return _format_results(results)
