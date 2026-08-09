"""Read-only memory inspection for the interactive path.

Plain functions over a committed workspace directory, with no Claude SDK
wrapper and no model calls, so both the MCP server and the CLI can use them.
Every result is size-bounded: a memory file can be arbitrarily large and a
tool result must not be.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..management.transaction import TransactionError, workspace_revision
from ..workspace_layout import is_internal_path
from ..markdown import parse_topic_tree

DERIVED_DIRS = ("timeline",)
DERIVED_FILES = ("recent_events.jsonl", "relations.json")
MAX_SNIPPET_CHARS = 300
MAX_READ_LINES = 400
MAX_READ_CHARS = 40_000


@dataclass(frozen=True)
class InspectLimits:
    max_list_entries: int = 500
    max_grep_matches: int = 200
    max_read_chars: int = MAX_READ_CHARS


def resolve_path(memory_dir: Path, raw: object) -> Path:
    """Resolve a workspace-relative path, refusing any escape."""
    root = Path(memory_dir).resolve()
    text = str(raw or "").strip()
    if not text:
        raise TransactionError("INVALID_ARGUMENT", "path is required")
    relative = Path(text)
    if relative.is_absolute() or ".." in relative.parts:
        raise TransactionError(
            "PATH_OUTSIDE_WORKSPACE", "path escapes the workspace", path=text
        )
    candidate = root / relative
    if candidate.is_symlink():
        raise TransactionError(
            "PATH_OUTSIDE_WORKSPACE", "path is a symlink", path=text
        )
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise TransactionError(
            "PATH_OUTSIDE_WORKSPACE", "path escapes the workspace", path=text
        ) from exc
    return resolved


def visible_files(memory_dir: Path) -> list[Path]:
    root = Path(memory_dir).resolve()
    result = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if is_internal_path(relative):
            continue
        result.append(path)
    return sorted(result, key=lambda item: item.relative_to(root).as_posix())


def status(memory_dir: Path, *, embedding_available: bool = False) -> dict[str, Any]:
    """Committed workspace counts. Never rebuilds and never calls a model."""
    root = Path(memory_dir).resolve()
    units = parse_topic_tree(root / "topics") if (root / "topics").is_dir() else []
    files = visible_files(root)
    recent = root / "recent_events.jsonl"
    recent_count = 0
    if recent.is_file():
        recent_count = sum(
            1 for line in recent.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    relations = root / "relations.json"
    relation_count = 0
    if relations.is_file():
        try:
            payload = json.loads(relations.read_text(encoding="utf-8"))
            relation_count = len(payload) if isinstance(payload, (list, dict)) else 0
        except ValueError:
            relation_count = 0
    return {
        "workspace": str(root),
        "revision": workspace_revision(root),
        "topic_files": sum(
            1 for path in files
            if path.relative_to(root).parts[:1] == ("topics",)
        ),
        "blocks": len(units),
        "source_files": sum(
            1 for path in files
            if path.relative_to(root).parts[:1] == ("sources",)
        ),
        "timeline_files": sum(
            1 for path in files
            if path.relative_to(root).parts[:1] == ("timeline",)
        ),
        "recent_events": recent_count,
        "relations": relation_count,
        "core_exists": (root / "core.md").is_file(),
        "embedding_available": embedding_available,
    }


def list_files(
    memory_dir: Path,
    *,
    prefix: str = "",
    include_derived: bool = True,
    limit: int = 200,
    limits: InspectLimits | None = None,
) -> dict[str, Any]:
    limits = limits or InspectLimits()
    root = Path(memory_dir).resolve()
    capped = max(1, min(int(limit), limits.max_list_entries))
    normalized = str(prefix or "").strip().lstrip("/")
    entries = []
    for path in visible_files(root):
        relative = path.relative_to(root).as_posix()
        if normalized and not relative.startswith(normalized):
            continue
        if not include_derived and _is_derived(relative):
            continue
        entries.append({
            "path": relative,
            "bytes": path.stat().st_size,
            "derived": _is_derived(relative),
        })
    return {
        "files": entries[:capped],
        "total": len(entries),
        "truncated": len(entries) > capped,
    }


def _is_derived(relative: str) -> bool:
    head = relative.split("/", 1)[0]
    return head in DERIVED_DIRS or relative in DERIVED_FILES


def read_file(
    memory_dir: Path,
    path: str,
    *,
    heading: str | None = None,
    block_id: str | None = None,
    offset: int = 1,
    limit: int | None = None,
    limits: InspectLimits | None = None,
) -> dict[str, Any]:
    limits = limits or InspectLimits()
    selectors = [value for value in (heading, block_id) if value]
    explicit_window = limit is not None or offset != 1
    if len(selectors) > 1 or (selectors and explicit_window):
        raise TransactionError(
            "INVALID_ARGUMENT",
            "use at most one of heading, block_id or offset/limit",
        )
    target = resolve_path(memory_dir, path)
    if not target.is_file():
        raise TransactionError(
            "INVALID_ARGUMENT", "file does not exist", path=str(path)
        )
    text = target.read_text(encoding="utf-8")
    if block_id:
        content = _extract_block(text, block_id, path=str(path))
        mode = "block"
    elif heading:
        content = _extract_heading(text, heading, path=str(path))
        mode = "heading"
    else:
        lines = text.splitlines(keepends=True)
        start = max(int(offset) - 1, 0)
        count = MAX_READ_LINES if limit is None else max(1, int(limit))
        content = "".join(lines[start:start + count])
        mode = "lines"
    truncated = len(content) > limits.max_read_chars
    return {
        "path": Path(str(path)).as_posix(),
        "mode": mode,
        "content": content[:limits.max_read_chars],
        "truncated": truncated,
    }


def _extract_block(text: str, block_id: str, *, path: str) -> str:
    lines = text.splitlines()
    anchor = None
    for index, line in enumerate(lines):
        if re.search(rf"\^{re.escape(block_id)}\s*$", line):
            anchor = index
            break
    if anchor is None:
        raise TransactionError(
            "INVALID_ARGUMENT",
            f"block not found: {block_id}",
            path=path,
        )
    start = anchor
    while start > 0 and lines[start - 1].strip():
        start -= 1
    end = anchor
    while end + 1 < len(lines) and lines[end + 1].strip():
        end += 1
    body = lines[start:end + 1]
    # A block is only interpretable with the footnotes it cites.
    cited = set(re.findall(r"\[\^([A-Za-z0-9_-]+)\]", "\n".join(body)))
    footnotes = [
        line for line in lines
        if any(line.startswith(f"[^{name}]:") for name in cited)
    ]
    return "\n".join(body + ([""] + footnotes if footnotes else [])) + "\n"


def _extract_heading(text: str, heading: str, *, path: str) -> str:
    lines = text.splitlines()
    wanted = heading.strip().lstrip("#").strip().casefold()
    start = None
    level = 0
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match and match.group(2).strip().casefold() == wanted:
            start = index
            level = len(match.group(1))
            break
    if start is None:
        raise TransactionError(
            "INVALID_ARGUMENT", f"heading not found: {heading}", path=path
        )
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = re.match(r"^(#{1,6})\s+", lines[index])
        if match and len(match.group(1)) <= level:
            end = index
            break
    return "\n".join(lines[start:end]).rstrip() + "\n"


def grep(
    memory_dir: Path,
    query: str,
    *,
    prefix: str = "",
    case_sensitive: bool = False,
    literal: bool = True,
    limit: int = 50,
    limits: InspectLimits | None = None,
) -> dict[str, Any]:
    """Search memory text in Python. Never shells out to system grep."""
    limits = limits or InspectLimits()
    if not str(query or "").strip():
        raise TransactionError("INVALID_ARGUMENT", "query is required")
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern_text = re.escape(query) if literal else query
    try:
        pattern = re.compile(pattern_text, flags)
    except re.error as exc:
        raise TransactionError(
            "INVALID_ARGUMENT", f"invalid regular expression: {exc}"
        ) from exc
    root = Path(memory_dir).resolve()
    normalized = str(prefix or "").strip().lstrip("/")
    capped = max(1, min(int(limit), limits.max_grep_matches))
    matches = []
    total = 0
    for path in visible_files(root):
        relative = path.relative_to(root).as_posix()
        if normalized and not relative.startswith(normalized):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if not pattern.search(line):
                continue
            total += 1
            if len(matches) < capped:
                matches.append({
                    "path": relative,
                    "line": number,
                    "text": line.strip()[:MAX_SNIPPET_CHARS],
                })
    return {"matches": matches, "total": total, "truncated": total > len(matches)}


def search(
    memory_dir: Path,
    query: str,
    *,
    method: str = "bm25",
    top_k: int = 8,
    path_prefix: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    if method not in ("bm25", "embedding"):
        raise TransactionError(
            "INVALID_ARGUMENT", "method must be bm25 or embedding"
        )
    if not str(query or "").strip():
        raise TransactionError("INVALID_ARGUMENT", "query is required")
    capped = max(1, min(int(top_k), 10))
    root = Path(memory_dir).resolve()
    if method == "embedding":
        return {"method": "embedding", "results": _embedding_search(
            root, query, capped, path_prefix, date_from, date_to
        )}
    from .bm25 import MemoryBM25Index

    index = MemoryBM25Index(root, persist=False)
    hits = index.search(
        query,
        top_k=capped,
        path_prefix=path_prefix,
        date_from=date_from,
        date_to=date_to,
    )
    return {"method": "bm25", "results": [_present(hit) for hit in hits]}


def _embedding_search(
    root: Path,
    query: str,
    top_k: int,
    path_prefix: str | None,
    date_from: str | None,
    date_to: str | None,
) -> list[dict[str, Any]]:
    try:
        from .embedding import MemoryEmbeddingIndex

        index = MemoryEmbeddingIndex(root)
        # The encoder loads lazily, so an absent backend only surfaces here.
        hits = index.search(query, top_k=top_k, date_from=date_from, date_to=date_to)
    except Exception as exc:
        # Never silently fall back to BM25: the caller asked for embeddings
        # and a different method would be a different answer.
        raise TransactionError(
            "EMBEDDING_UNAVAILABLE", f"embedding search unavailable: {exc}"
        ) from exc
    if path_prefix:
        # Same prefix semantics as BM25; embedding search has no built-in filter.
        from .bm25 import _normalize_path_prefix

        normalized = _normalize_path_prefix(str(path_prefix))
        hits = [
            hit for hit in hits
            if str(hit.get("path", "")).startswith(normalized)
        ]
    return [_present(hit) for hit in hits]


def _present(hit: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "event_id", "path", "line", "headings", "date", "dates",
        "content", "refs", "final_score", "score",
    )
    result = {key: hit[key] for key in keep if key in hit}
    if isinstance(result.get("content"), str):
        result["content"] = result["content"][:MAX_SNIPPET_CHARS * 4]
    return result
