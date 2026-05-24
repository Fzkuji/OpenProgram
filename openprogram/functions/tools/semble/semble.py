"""semble function — semantic + lexical code search.

Wraps the `semble` Python library (tree-sitter chunking + Model2Vec
static embeddings + BM25, fused with reciprocal rank fusion). Returns
ranked code chunks instead of full files, so the LLM gets the
relevant region without paying for surrounding noise.

Two tools:
  - `semble_search` — natural-language or code query → ranked chunks
  - `semble_find_related` — given a file:line, find similar chunks

Each repo path gets one SembleIndex, built on first call (seconds)
and cached for the worker's lifetime (subsequent calls are
millisecond-level).
"""
from __future__ import annotations

import os
import threading
from typing import Any, Optional

from openprogram.functions._runtime import function


_DESCRIPTION_SEARCH = (
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

_DESCRIPTION_FIND_RELATED = (
    "Given a file path and line number, return chunks semantically "
    "similar to the code at that location. Useful after "
    "`semble_search` to discover related implementations or "
    "alternative call sites.\n"
    "\n"
    "- `file_path` may be absolute or relative to `path`.\n"
    "- `line` is 1-based."
)


# Per-repo SembleIndex cache. Building an index is O(seconds) for a
# medium repo (tree-sitter parse + embed every chunk); cache amortises
# that across calls. Per-path lock prevents two concurrent first-calls
# on the same repo from each building their own index.
_index_cache: dict[str, Any] = {}
_index_locks: dict[str, threading.Lock] = {}
_cache_master_lock = threading.Lock()


def _resolve_path(path: Optional[str]) -> str:
    if path:
        return os.path.abspath(path)
    try:
        from openprogram.paths import get_default_workdir
        return get_default_workdir()
    except Exception:
        return os.getcwd()


def _get_or_build_index(path: str) -> Any:
    from semble import SembleIndex

    cached = _index_cache.get(path)
    if cached is not None:
        return cached
    with _cache_master_lock:
        lk = _index_locks.setdefault(path, threading.Lock())
    with lk:
        cached = _index_cache.get(path)
        if cached is not None:
            return cached
        idx = SembleIndex.from_path(path)
        _index_cache[path] = idx
        return idx


def _format_results(results: list) -> str:
    if not results:
        return "No matches"
    out: list[str] = []
    for r in results:
        c = r.chunk
        head = f"## {c.file_path}:{c.start_line}-{c.end_line}"
        score = getattr(r, "score", None)
        if score is not None:
            head += f"  [score={score:.3f}]"
        out.append(head)
        out.append("```")
        out.append(c.content)
        out.append("```")
        out.append("")
    return "\n".join(out)


@function(
    name="semble_search",
    description=_DESCRIPTION_SEARCH,
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


@function(
    name="semble_find_related",
    description=_DESCRIPTION_FIND_RELATED,
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
