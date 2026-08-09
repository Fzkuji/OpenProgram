"""Dispatch Scriptorium retrieval tool calls."""

from pathlib import Path
from typing import Any

from .bm25 import MemoryBM25Index
from .bm25 import render_search_results as render_bm25_results
from .embedding import MemoryEmbeddingIndex
from .embedding import render_search_results as render_embedding_results
from .fusion import fused_search

from .shell import normalize_workspace_command, validate_read_only_command


def _search_index(
    runtime: Any,
    kind: str,
    *,
    memory_dir: Path,
    files: list[Path],
    indexes: dict[str, Any],
) -> Any:
    if kind in indexes:
        return indexes[kind]
    relative_files = tuple(sorted(
        path.relative_to(memory_dir).as_posix()
        for path in files
        if path.suffix == ".md"
        and path.relative_to(memory_dir).parts[0] in {"topics", "sources"}
    ))
    if kind == "bm25":
        factory = lambda: MemoryBM25Index(
            memory_dir, persist=False, files=files
        )
    elif kind == "embedding":
        factory = lambda: MemoryEmbeddingIndex(memory_dir, files=files)
    else:
        raise ValueError(f"unknown search index: {kind}")
    getter = getattr(runtime, "get_retrieval_index", None)
    indexes[kind] = (
        getter((kind, str(memory_dir.resolve()), relative_files), factory)
        if callable(getter)
        else factory()
    )
    return indexes[kind]


def execute_tool_call(
    runtime: Any,
    name: str,
    args: dict[str, Any],
    *,
    memory_dir: Path,
    files: list[Path],
    condition: str,
    include_recent: bool,
    indexes: dict[str, Any],
) -> tuple[str, bool, bool | None]:
    if name == "bash":
        command = normalize_workspace_command(
            args.get("command", ""), memory_dir
        )
        accepted, reason = validate_read_only_command(command)
        if not accepted:
            return f"Command rejected: {reason}", False, False
        output = runtime.execute_tool(
            "bash",
            {
                **args,
                "command": command,
            },
            str(memory_dir),
            hide_raw=True,
        )
        return output, True, True
    if name in {"bm25_search", "embedding_search", "memory_search"}:
        query = str(args.get("query", "")).strip()
        if not query:
            raise ValueError("search query is empty")
        top_k = max(1, min(int(args.get("top_k", 10)), 10))
        if name == "memory_search":
            results = fused_search(
                _search_index(
                    runtime, "bm25",
                    memory_dir=memory_dir, files=files, indexes=indexes,
                ),
                _search_index(
                    runtime, "embedding",
                    memory_dir=memory_dir, files=files, indexes=indexes,
                ),
                query,
                top_k=top_k,
                path_prefix=args.get("path_prefix") or None,
                date_from=args.get("date_from") or None,
                date_to=args.get("date_to") or None,
            )
            rendered = render_bm25_results(results)
            if rendered == "No BM25 matches.":
                rendered = "No memory matches."
            return rendered, True, None
        if name == "bm25_search":
            index = _search_index(
                runtime,
                "bm25",
                memory_dir=memory_dir,
                files=files,
                indexes=indexes,
            )
            results = index.search(
                query,
                top_k=top_k,
                path_prefix=args.get("path_prefix") or None,
                date_from=args.get("date_from") or None,
                date_to=args.get("date_to") or None,
            )
            return render_bm25_results(results), True, None
        index = _search_index(
            runtime,
            "embedding",
            memory_dir=memory_dir,
            files=files,
            indexes=indexes,
        )
        results = index.search(
            query,
            top_k=top_k,
            date_from=args.get("date_from") or None,
            date_to=args.get("date_to") or None,
        )
        return render_embedding_results(results), True, None
    raise ValueError(f"unknown tool: {name}")
