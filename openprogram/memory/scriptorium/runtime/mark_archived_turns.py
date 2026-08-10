"""One-time migration from legacy cursors to exact source-node markers."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .state import RuntimeStateStore


SOURCE_HEADER = re.compile(
    r'(?:\A|\n\n)<a id="(source-[0-9a-f]{16})"></a>\n'
    r"<!-- source-id:(openprogram/([^/\n]+)/([^>\n]+)) -->\n"
)
MARKER = "memory_written_scriptorium"


def _payload(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _archived_node_ids(archive: Path) -> tuple[str, list[str]] | None:
    """Validated session and node IDs from provider source headers."""
    try:
        text = archive.read_text(encoding="utf-8")
    except OSError:
        return None
    session_id = unquote(archive.stem)
    if not text.startswith(f"# {session_id}\n"):
        return None
    node_ids: list[str] = []
    for match in SOURCE_HEADER.finditer(text):
        anchor, source_id, source_session, node_id = match.groups()
        expected_anchor = (
            "source-" + hashlib.sha256(source_id.encode()).hexdigest()[:16]
        )
        if source_session == session_id and anchor == expected_anchor:
            node_ids.append(node_id.strip())
    return session_id, node_ids


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
        grouped: dict[str, dict[str, dict[str, str]]] = {}
        if source_dir.exists():
            for archive in sorted(source_dir.glob("*.md")):
                parsed = _archived_node_ids(archive)
                if parsed is None:
                    continue
                session_id, node_ids = parsed
                grouped.setdefault(session_id, {}).update({
                    node_id: {MARKER: workspace_id}
                    for node_id in node_ids
                })
        for session_id, patches in grouped.items():
            session_store.merge_node_metadata_batch(session_id, patches)
        state_store.save(state_store.load())
    return True
