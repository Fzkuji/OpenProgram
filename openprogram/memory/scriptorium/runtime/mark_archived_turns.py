"""One-time migration from legacy cursors to exact source-node markers."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .state import RuntimeStateStore


SOURCE_ID = re.compile(
    r"<!-- source-id:openprogram/([^/\n]+)/([^>\n]+) -->"
)
MARKER = "memory_written_scriptorium"


def _payload(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


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
        if source_dir.exists():
            for archive in sorted(source_dir.glob("*.md")):
                try:
                    text = archive.read_text(encoding="utf-8")
                except OSError:
                    continue
                for session_id, node_id in SOURCE_ID.findall(text):
                    session_store.merge_node_metadata(
                        session_id,
                        node_id.strip(),
                        {MARKER: workspace_id},
                    )
        state_store.save(state_store.load())
    return True
