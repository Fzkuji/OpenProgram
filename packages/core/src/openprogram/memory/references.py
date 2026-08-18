"""Stable, read-only references to current Topic memory blocks."""

from __future__ import annotations

import re
from typing import Any

from . import store
from .markdown import parse_topic_tree


MAX_REFS = 16
_MEMORY_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def list_refs(query: str = "", *, limit: int = 100) -> list[dict[str, Any]]:
    """List current blocks in a transport-safe reference shape."""
    workspace_id = store.workspace_id()
    wanted = query.strip().casefold()
    rows = []
    for unit in parse_topic_tree(store.topics_dir()):
        if wanted and wanted not in " ".join((
            unit.memory_id, unit.topic_path, unit.content,
        )).casefold():
            continue
        rows.append({
            "workspace_id": workspace_id,
            "memory_id": unit.memory_id,
            "topic_path": unit.topic_path,
            "content": unit.content,
            "when": unit.when,
        })
        if len(rows) >= max(1, min(int(limit), 500)):
            break
    return rows


def normalize(refs: Any) -> list[dict[str, str]]:
    if refs in (None, []):
        return []
    if not isinstance(refs, list) or len(refs) > MAX_REFS:
        raise ValueError(f"memory_refs must be a list with at most {MAX_REFS} items")
    normalized = []
    seen = set()
    for ref in refs:
        if not isinstance(ref, dict):
            raise ValueError("each memory ref must be an object")
        workspace_id = str(ref.get("workspace_id") or "").strip()
        memory_id = str(ref.get("memory_id") or "").strip()
        if not workspace_id or not _MEMORY_ID.fullmatch(memory_id):
            raise ValueError("memory ref requires workspace_id and a valid memory_id")
        key = (workspace_id, memory_id)
        if key not in seen:
            normalized.append({
                "workspace_id": workspace_id,
                "memory_id": memory_id,
            })
            seen.add(key)
    return normalized


def resolve(refs: Any) -> list[dict[str, Any]]:
    """Resolve refs against the current workspace on every call."""
    normalized = normalize(refs)
    if not normalized:
        return []
    current_workspace = store.workspace_id()
    foreign = [
        ref for ref in normalized if ref["workspace_id"] != current_workspace
    ]
    if foreign:
        raise ValueError("memory ref belongs to a different workspace")
    units = {unit.memory_id: unit for unit in parse_topic_tree(store.topics_dir())}
    resolved = []
    for ref in normalized:
        unit = units.get(ref["memory_id"])
        if unit is None:
            raise ValueError(f"memory ref not found: {ref['memory_id']}")
        resolved.append({
            **ref,
            "topic_path": unit.topic_path,
            "content": unit.content,
            "when": unit.when,
        })
    return resolved


def render_context(refs: Any, *, max_chars: int = 12_000) -> str:
    rows = resolve(refs)
    if not rows:
        return ""
    intro = [
        "Referenced Memory (current content):",
        "When a referenced record describes this scheduled task, keep its "
        "status current with memory_status, memory_get, and memory_update; "
        "delete it only when no durable value remains.",
    ]
    prefixes = [
        f"- [{row['memory_id']}] topics/{row['topic_path']}: " for row in rows
    ]
    fixed_size = len("\n".join([*intro, *prefixes]))
    if fixed_size > max_chars:
        raise ValueError("max_chars is too small to identify every memory ref")
    remaining = max_chars - fixed_size
    marker = " … [content truncated]"
    lines = list(intro)
    for index, (row, prefix) in enumerate(zip(rows, prefixes)):
        quota = remaining // (len(rows) - index)
        content = str(row["content"])
        if len(content) > quota:
            if quota < len(marker):
                raise ValueError(
                    "max_chars is too small to mark truncated memory refs"
                )
            content = content[:quota - len(marker)].rstrip() + marker
        lines.append(prefix + content)
        remaining -= len(content)
    return "\n".join(lines)


__all__ = ["MAX_REFS", "list_refs", "normalize", "resolve", "render_context"]
