"""Memory tools — reading and correcting the Markdown memory workspace.

Memory is written in the background, so there is no tool here for
"save this". What the agent needs mid-conversation is to look things up,
and occasionally to fix something it can see is wrong or to record a
fact the user asked it to remember right now.

Everything is a file: ``topics/**/*.md`` is the editable semantic
memory, ``sources/**`` the read-only evidence it cites, and
``timeline/`` a derived view rebuilt after every write.
"""

from __future__ import annotations

import json
from contextlib import closing
from typing import Any

from openprogram.memory import store
from openprogram.memory.scriptorium.management import MemoryWorkspace
from openprogram.memory.scriptorium.management.transaction import (
    TransactionError,
    workspace_revision,
)
from openprogram.memory.provider import sanitize_context
from openprogram.memory.scriptorium.retrieval import inspect

MAX_SNIPPETS = 8


def _root():
    return store.ensure()


def _fail(exc: TransactionError) -> str:
    return json.dumps(
        {"ok": False, "error": {"code": exc.code, "message": exc.message,
                                "path": exc.path}},
        ensure_ascii=False,
    )


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


# -- search ----------------------------------------------------------------

SEARCH_NAME = "memory_search"
SEARCH_DESC = (
    "Search memory by meaning and return the matching paragraphs with "
    "their file paths and block IDs. Use this when you know what you are "
    "looking for but not how it was worded. For an exact name, ID or "
    "phrase use `memory_grep` instead."
)
SEARCH_SPEC: dict[str, Any] = {
    "name": SEARCH_NAME,
    "description": SEARCH_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "what to look for"},
            "top_k": {"type": "integer", "description": "results, max 10"},
            "path_prefix": {
                "type": "string",
                "description": "restrict to a subtree, e.g. topics/people/",
            },
        },
        "required": ["query"],
    },
}


def memory_search(
    query: str | None = None,
    top_k: int = MAX_SNIPPETS,
    path_prefix: str | None = None,
    **_: Any,
) -> str:
    if not (query or "").strip():
        return "memory_search needs a query."
    try:
        found = inspect.search(
            _root(), query, top_k=int(top_k or MAX_SNIPPETS),
            path_prefix=path_prefix or None,
        )
    except TransactionError as exc:
        return _fail(exc)
    results = found.get("results", [])
    if not results:
        return f"No memory matches {query!r}."
    lines = []
    for hit in results:
        where = hit.get("path", "?")
        block = hit.get("event_id")
        head = f"{where}" + (f"#^{block}" if block else "")
        lines.append(f"--- {head}\n{sanitize_context(hit.get('content', ''))}")
    return "\n\n".join(lines)


# -- grep ------------------------------------------------------------------

GREP_NAME = "memory_grep"
GREP_DESC = (
    "Find an exact string in memory — a name, an identifier, a quoted "
    "phrase. Returns matching lines with their file and line number."
)
GREP_SPEC: dict[str, Any] = {
    "name": GREP_NAME,
    "description": GREP_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "prefix": {"type": "string", "description": "restrict to a subtree"},
            "case_sensitive": {"type": "boolean"},
        },
        "required": ["query"],
    },
}


def memory_grep(
    query: str | None = None,
    prefix: str = "",
    case_sensitive: bool = False,
    **_: Any,
) -> str:
    if not (query or "").strip():
        return "memory_grep needs a query."
    try:
        found = inspect.grep(
            _root(), query, prefix=prefix or "",
            case_sensitive=bool(case_sensitive),
        )
    except TransactionError as exc:
        return _fail(exc)
    matches = found.get("matches", [])
    if not matches:
        return f"No line in memory contains {query!r}."
    return "\n".join(
        f"{m.get('path')}:{m.get('line')}: {m.get('text', '').strip()}"
        for m in matches
    )


# -- read ------------------------------------------------------------------

GET_NAME = "memory_get"
GET_DESC = (
    "Read a memory file, or one section or block of it. Pass `heading` "
    "for a single section and `block_id` for a single paragraph with the "
    "footnotes it cites — reading a whole file is rarely what you want."
)
GET_SPEC: dict[str, Any] = {
    "name": GET_NAME,
    "description": GET_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "e.g. topics/people/dave.md"},
            "heading": {"type": "string"},
            "block_id": {"type": "string", "description": "without the ^"},
        },
        "required": ["path"],
    },
}


def memory_get(
    path: str | None = None,
    heading: str | None = None,
    block_id: str | None = None,
    **_: Any,
) -> str:
    if not (path or "").strip():
        return "memory_get needs a path. Use `memory_browse` to see them."
    try:
        found = inspect.read_file(
            _root(), path, heading=heading or None,
            block_id=(block_id or None),
        )
    except TransactionError as exc:
        return _fail(exc)
    return sanitize_context(found.get("content", "")) or "(empty)"


# -- browse ----------------------------------------------------------------

BROWSE_NAME = "memory_browse"
BROWSE_DESC = (
    "List what memory holds: the topic files, the archived sources, and "
    "the derived timeline. Start here when you do not know what exists."
)
BROWSE_SPEC: dict[str, Any] = {
    "name": BROWSE_NAME,
    "description": BROWSE_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "prefix": {
                "type": "string",
                "description": "restrict to a subtree, e.g. topics/",
            },
        },
        "required": [],
    },
}


def memory_browse(prefix: str = "", **_: Any) -> str:
    try:
        listing = inspect.list_files(_root(), prefix=prefix or "")
    except TransactionError as exc:
        return _fail(exc)
    files = listing.get("files", [])
    if not files:
        return (
            "Memory is empty. It fills in on its own as you talk — "
            "there is nothing to do about this."
        )
    lines = [f"{len(files)} file(s):", ""]
    lines += [
        f"  {f.get('path')}"
        + (f"  ({f.get('blocks')} blocks)" if f.get("blocks") else "")
        for f in files
    ]
    if listing.get("truncated"):
        lines.append("  … truncated; narrow with `prefix`.")
    return "\n".join(lines)


# -- update ----------------------------------------------------------------

UPDATE_NAME = "memory_update"
UPDATE_DESC = (
    "Correct or add one thing in memory. Conversation is written up in "
    "the background, so use this only for what the user asked you to "
    "remember right now, or to fix something you can see is wrong. "
    "Send a unified diff over topics/**/*.md or core.md, with the "
    "revision you read from `memory_status`."
)
UPDATE_SPEC: dict[str, Any] = {
    "name": UPDATE_NAME,
    "description": UPDATE_DESC,
    "parameters": {
        "type": "object",
        "properties": {
            "base_revision": {"type": "string"},
            "patch": {"type": "string", "description": "unified diff"},
            "sources": {
                "type": "array",
                "description": "quoted statements the edit rests on",
                "items": {"type": "object"},
            },
            "commit_message": {"type": "string"},
        },
        "required": ["base_revision", "patch"],
    },
}


def memory_update(
    base_revision: str | None = None,
    patch: str | None = None,
    sources: list[dict[str, Any]] | None = None,
    commit_message: str | None = None,
    **_: Any,
) -> str:
    if not (base_revision or "").strip() or not (patch or "").strip():
        return "memory_update needs base_revision and patch."
    try:
        # The workspace stages a copy of memory under the temp directory;
        # dropped without closing, one copy is left behind per call.
        with closing(MemoryWorkspace(_root())) as space:
            result = space.update(
                base_revision=base_revision,
                patch=patch,
                sources=sources,
                commit_message=commit_message,
            )
    except TransactionError as exc:
        return _fail(exc)
    return _dump({
        "ok": True,
        "revision": result.revision,
        "block_ids": result.block_ids,
        "changed_files": result.changed_files,
    })


# -- status ----------------------------------------------------------------

STATUS_NAME = "memory_status"
STATUS_DESC = (
    "How much memory exists and the current revision, which "
    "`memory_update` needs."
)
STATUS_SPEC: dict[str, Any] = {
    "name": STATUS_NAME, "description": STATUS_DESC,
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def memory_status(**_: Any) -> str:
    root = _root()
    try:
        return _dump(inspect.status(root))
    except TransactionError as exc:
        return _fail(exc)
    except Exception:  # noqa: BLE001
        return _dump({"workspace": str(root),
                      "revision": workspace_revision(root)})


__all__ = [
    "SEARCH_NAME", "SEARCH_SPEC", "memory_search",
    "GREP_NAME", "GREP_SPEC", "memory_grep",
    "GET_NAME", "GET_SPEC", "memory_get",
    "BROWSE_NAME", "BROWSE_SPEC", "memory_browse",
    "UPDATE_NAME", "UPDATE_SPEC", "memory_update",
    "STATUS_NAME", "STATUS_SPEC", "memory_status",
]
