"""Read-only memory inspection for the interactive path.

Plain functions over a committed workspace directory, with no Claude SDK
wrapper and no model calls, so both the MCP server and the CLI can use them.
Every result is size-bounded: a memory file can be arbitrarily large and a
tool result must not be.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..management.transaction import TransactionError, workspace_revision
from ..workspace_layout import is_internal_path
from ..markdown import read_topic_tree
from .fusion import CANDIDATE_MULTIPLIER, embedding_weight, fuse

DERIVED_DIRS = ("timeline",)
DERIVED_FILES = ("recent_events.jsonl", "relations.json")
MAX_SNIPPET_CHARS = 300
MAX_READ_LINES = 400
MAX_READ_CHARS = 40_000

_search_index_lock = threading.RLock()
_bm25_indexes: dict[Path, tuple[tuple[tuple[Any, ...], ...], Any]] = {}
_embedding_indexes: dict[Path, tuple[tuple[tuple[Any, ...], ...], Any]] = {}


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


def workspace_identity(memory_dir: Path) -> str:
    """A stable name for a workspace that discloses nothing about the host.

    The absolute path names the account, the home directory layout and
    often the person; it is the owner's own information and belongs in
    the owner's own surfaces, not in a tool result a model can quote back
    into a channel. Callers that need somewhere to point a human — the
    CLI, the web UI — read ``workspace_path`` instead.
    """
    from ..workspace_layout import runtime_dir

    identity = runtime_dir(Path(memory_dir)) / "workspace-id"
    try:
        value = identity.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    return value or "workspace"


def status(
    memory_dir: Path,
    *,
    embedding_available: bool | None = None,
    include_path: bool = False,
) -> dict[str, Any]:
    """Committed workspace counts. Never rebuilds and never calls a model.

    ``include_path`` adds the on-disk location under ``workspace_path``.
    Off by default so the model-facing tool cannot leak it by omission;
    the owner-only CLI and web views ask for it explicitly.
    """
    root = Path(memory_dir).resolve()
    # Read the way memory is read: whatever a file holds, the overview
    # counts what can be read.
    units = (
        read_topic_tree(root / "topics").units
        if (root / "topics").is_dir() else []
    )
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
    from ..management.verification import status as verification_status
    from ..runtime.writer_status import status as writer_status

    return {
        "workspace": workspace_identity(root),
        **({"workspace_path": str(root)} if include_path else {}),
        "revision": workspace_revision(root),
        "topic_files": sum(
            1 for path in files
            if path.relative_to(root).parts[:1] == ("topics",)
        ),
        "blocks": len(units),
        "timeline_files": sum(
            1 for path in files
            if path.relative_to(root).parts[:1] == ("timeline",)
        ),
        "recent_events": recent_count,
        "relations": relation_count,
        "core_exists": _has_core(root),
        "embedding_available": (
            embedding_is_available()
            if embedding_available is None
            else embedding_available
        ),
        "writer": writer_status(root),
        "verification": verification_status(root),
    }


def embedding_is_available() -> bool:
    """Whether semantic recall can run, without loading the encoder."""
    try:
        from . import embedding_model
    except Exception:
        return False
    if embedding_model.platform_unavailable_reason() is not None:
        return False
    return (
        embedding_model.packages_available()
        and embedding_model.default_model_is_cached()
    )


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
    text = target.read_text(encoding="utf-8", errors="replace")
    if block_id:
        content = _read_paragraph(memory_dir, target, text, block_id.lstrip("^"), str(path))
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


#: What a paragraph that cites nothing says about where it came from.
SOURCE_LINES = {
    "outside": "source: outside file",
    "person": "source: added by person",
}

CHANGED_NOTE = (
    "(This paragraph has changed since that ID was read; the file's "
    "current paragraphs follow.)"
)


def _read_paragraph(
    memory_dir: Path, target: Path, text: str, block_id: str, path: str,
) -> str:
    """One paragraph by ID, with the footnotes it cites, and a line naming
    its source when it cites nothing.

    A derived ID (``~…``, a paragraph without an ``^id`` of its own) names
    text that may have changed since it was read: then the file's current
    paragraphs come back instead of an error, as a citation to a file
    still leads to the file.
    """
    from ..management import ownership
    from ..markdown.parser import DERIVED_PREFIX, read_topic_file

    root = Path(memory_dir).resolve()
    topics = root / "topics"
    unit = None
    try:
        relative = target.relative_to(topics).as_posix()
    except ValueError:
        relative = None
    if relative is not None:
        units, _problem = read_topic_file(
            target, topics,
            outside=ownership.contains(ownership.outside_paths(root), relative),
        )
        unit = next((u for u in units if u.memory_id == block_id), None)
    if block_id.startswith(DERIVED_PREFIX):
        if unit is None:
            return f"{CHANGED_NOTE}\n\n{text.rstrip()}\n"
        lines = text.split("\n")
        start = max(unit.line - 1, 0)
        end = start
        while end + 1 < len(lines) and lines[end + 1].strip():
            end += 1
        content = "\n".join(lines[start:end + 1]) + "\n"
    else:
        content = _extract_block(text, block_id, path=path)
    origin = SOURCE_LINES.get(unit.origin) if unit is not None else None
    return f"{content}{origin}\n" if origin else content


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
    hybrid_weighting: str | None = None,
    file_boost: bool | None = None,
) -> dict[str, Any]:
    """Rank Topic blocks for ``query``.

    ``hybrid_weighting`` and ``file_boost`` tune ``method="hybrid"``; left
    unset they come from the Memory settings.
    """
    if method not in ("bm25", "embedding", "hybrid"):
        raise TransactionError(
            "INVALID_ARGUMENT", "method must be bm25, embedding or hybrid"
        )
    if not str(query or "").strip():
        raise TransactionError("INVALID_ARGUMENT", "query is required")
    capped = max(1, min(int(top_k), 10))
    root = Path(memory_dir).resolve()
    effective_prefix = path_prefix
    if method == "embedding":
        return {"method": "embedding", "results": _embedding_search(
            root, query, capped, effective_prefix, date_from, date_to,
        )}
    index = _bm25_index(root)
    # Hybrid ranks a wider pool than it returns, so fusion can reorder it.
    pool = capped * CANDIDATE_MULTIPLIER if method == "hybrid" else capped
    hits = index.search(
        query,
        top_k=pool,
        path_prefix=effective_prefix,
        date_from=date_from,
        date_to=date_to,
        refresh_index=False,
    )
    lexical = [_present(hit) for hit in hits]
    if method == "bm25":
        return {"method": "bm25", "results": lexical}
    semantic = _embedding_search(
        root, query, pool, effective_prefix, date_from, date_to,
    )
    if hybrid_weighting is None or file_boost is None:
        from ..management.config import load_memory_config

        config = load_memory_config()
        if hybrid_weighting is None:
            hybrid_weighting = config.hybrid_weighting
        if file_boost is None:
            file_boost = config.hybrid_file_boost
    return {
        "method": "hybrid",
        "results": fuse(
            lexical,
            semantic,
            embedding_share=embedding_weight(query, hybrid_weighting),
            file_boost=file_boost,
            top_k=capped,
        ),
    }


def _embedding_search(
    root: Path,
    query: str,
    top_k: int,
    path_prefix: str | None,
    date_from: str | None,
    date_to: str | None,
) -> list[dict[str, Any]]:
    try:
        index = _embedding_index(root)
        # The encoder loads lazily, so an absent backend only surfaces here.
        hits = index.search(
            query, top_k=top_k, date_from=date_from, date_to=date_to,
            path_prefix=path_prefix,
        )
    except Exception as exc:
        # Never silently fall back to BM25: the caller asked for embeddings
        # and a different method would be a different answer.
        raise TransactionError(
            "EMBEDDING_UNAVAILABLE", f"embedding search unavailable: {exc}"
        ) from exc
    return [_present(hit) for hit in hits]


def _index_signature(root: Path) -> tuple[tuple[Any, ...], ...]:
    from .bm25 import _indexable_files

    rows = []
    for relative, path in sorted(_indexable_files(root).items()):
        stat = path.stat()
        rows.append((
            relative,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
            stat.st_size,
            stat.st_ino,
        ))
    return tuple(rows)


def _bm25_index(root: Path) -> Any:
    from .bm25 import MemoryBM25Index

    signature = _index_signature(root)
    with _search_index_lock:
        cached = _bm25_indexes.get(root)
        if cached is not None and cached[0] == signature:
            return cached[1]
        index = MemoryBM25Index(root)
        _bm25_indexes[root] = (signature, index)
        return index


def _embedding_index(root: Path) -> Any:
    from .embedding import MemoryEmbeddingIndex

    signature = _index_signature(root)
    with _search_index_lock:
        cached = _embedding_indexes.get(root)
        if cached is not None and cached[0] == signature:
            return cached[1]
        index = MemoryEmbeddingIndex(root)
        _embedding_indexes[root] = (signature, index)
        return index


def _clear_search_index_cache_for_tests() -> None:
    with _search_index_lock:
        _bm25_indexes.clear()
        _embedding_indexes.clear()


def _present(hit: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "event_id", "path", "line", "headings", "date", "dates",
        "content", "refs", "origin", "final_score", "score", "similarity",
    )
    result = {key: hit[key] for key in keep if key in hit}
    if "event_id" not in result and hit.get("event"):
        result["event_id"] = hit["event"]
    if isinstance(result.get("content"), str):
        result["content"] = result["content"][:MAX_SNIPPET_CHARS * 4]
    return result


def _has_core(root) -> bool:
    """Whether ``MEMORY.md`` carries a rendered core (or an old root
    ``core.md`` still does)."""
    from ..runtime.memory_guide import GUIDE_FILE, split

    try:
        text = (root / GUIDE_FILE).read_text(encoding="utf-8")
    except OSError:
        text = ""
    return bool(split(text)[1]) or (root / "core.md").is_file()
