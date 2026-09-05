"""One-time migration from legacy cursors to exact source-node markers."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ..source_format import scan_source_archive
from ..writing import WRITTEN_NODE_MARKER
from .state import RuntimeStateStore


SOURCE_HEADER = re.compile(
    r'<a id="(source-[0-9a-f]{16})"></a>\n'
    r"<!-- source-id:(openprogram/([^/\n]+)/([^>\n]+)) -->\n"
)


def _payload(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _legacy_archived_node(archive: Path) -> tuple[str, str] | None:
    """The one legacy source header record content cannot precede."""
    try:
        text = archive.read_text(encoding="utf-8")
    except OSError:
        return None
    session_id = unquote(archive.stem)
    heading = f"# {session_id}\n\n"
    if not text.startswith(heading):
        return None
    match = SOURCE_HEADER.match(text, len(heading))
    if match is None:
        return None
    anchor, source_id, source_session, node_id = match.groups()
    expected_anchor = "source-" + hashlib.sha256(
        source_id.encode()
    ).hexdigest()[:16]
    if source_session != session_id or anchor != expected_anchor:
        return None
    return session_id, node_id.strip()


def _v2_archived_nodes(
    archive: Path, memory_dir: Path,
) -> list[tuple[str, str]]:
    """Session and node IDs from the archive's strictly valid prefix."""
    try:
        with archive.open("r", encoding="utf-8", newline="") as handle:
            text = handle.read()
        relative = archive.relative_to(memory_dir)
    except (OSError, ValueError):
        return []
    if "\r" in text:
        # Migration witnesses must preserve the original canonical framing.
        # General archive readers may normalize CRLF, but that is not proof
        # that an old cursor's exact bytes were committed by the v2 writer.
        return []
    nodes: list[tuple[str, str]] = []
    for frame in scan_source_archive(text, relative).frames:
        parts = frame.source_id.split("/", 2)
        if len(parts) == 3 and parts[0] == "openprogram":
            nodes.append((parts[1], parts[2]))
    return nodes


def _continuous_archived_prefixes(
    session_store, candidates: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Keep only archived prefixes under the live write-time turn filter."""
    from ..writing import _records

    grouped: dict[str, set[str]] = {}
    for session_id, node_ids in candidates.items():
        safe: set[str] = set()
        # ponytail: this one-time migration is O(candidates x path length).
        # Keep SessionStore's DAG semantics; add an index only if measured
        # migration duration requires it.
        for node_id in node_ids:
            branch = session_store.get_branch(session_id, node_id) or []
            for record in _records(session_id, branch):
                if record.message_id not in node_ids:
                    break
                safe.add(record.message_id)
        if safe:
            grouped[session_id] = safe
    return grouped


def migrate(memory_dir: Path, session_store, workspace_id: str) -> bool:
    """Seed markers from archived source IDs and then remove cursors."""
    state_store = RuntimeStateStore(Path(memory_dir))
    if "cursors" not in (_payload(state_store.path) or {}):
        return False

    from ..management.transaction import workspace_write_lock

    with workspace_write_lock(Path(memory_dir)):
        payload = _payload(state_store.path)
        if payload is None or "cursors" not in payload:
            return False
        source_dir = Path(memory_dir) / "sources" / "openprogram"
        candidates: dict[str, set[str]] = {}
        if source_dir.exists():
            for archive in sorted(source_dir.glob("*.md")):
                parsed = _legacy_archived_node(archive)
                if parsed is None:
                    continue
                session_id, node_id = parsed
                candidates.setdefault(session_id, set()).add(node_id)
            for archive in sorted((source_dir / "_v2").glob("*.md")):
                for session_id, node_id in _v2_archived_nodes(
                    archive, Path(memory_dir),
                ):
                    candidates.setdefault(session_id, set()).add(node_id)
        grouped = _continuous_archived_prefixes(session_store, candidates)
        for session_id, node_ids in grouped.items():
            patches = {
                node_id: {WRITTEN_NODE_MARKER: workspace_id} for node_id in node_ids
            }
            session_store.merge_node_metadata_batch(session_id, patches)
        state_store.save(state_store.load())
    return True
