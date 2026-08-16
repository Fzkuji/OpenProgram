"""Shared index machinery for the semble tool family.

Wraps the `semble` Python library (tree-sitter chunking + Model2Vec
static embeddings + BM25, fused with reciprocal rank fusion). Each repo
path gets one SembleIndex, built on first call (seconds) and cached for
the worker's lifetime (subsequent calls are millisecond-level).
"""
from __future__ import annotations

import os
import threading
from typing import Any, Optional


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
    # semble ships as the `[search]` extra, not a base dep — a leaf tool
    # does not get to put tree-sitter + an embedding model in every
    # install. Absent package → answer with the install line.
    try:
        from semble import SembleIndex
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "The bundled semantic search backend is unavailable. "
            "Reinstall the complete OpenProgram release."
        ) from exc

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
