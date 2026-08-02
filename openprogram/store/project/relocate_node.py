"""Record a project relocation as a DAG node (dag/overview.md §7).

A session's main working directory freezes on its first turn, so the one
thing that may still change it is a **repair**: the bound project's
folder was moved or renamed on disk and the user points OpenProgram at
the new location. That is a change to where every later turn runs, so it
goes into the graph rather than mutating the registry silently.

Shape (§3), the same as ``context/system_prompt``: ``role=code``,
``caller="ROOT"``, ``predecessor=None``. The write invariant constrains
only conversational nodes (role user/llm), and because ``caller`` is set
the store does not advance head — recording a relocation never moves the
branch tip.

The name is ``project/relocate``, deliberately NOT under ``context/``:
context nodes are pipeline machinery hidden from the transcript, while a
relocation is a user action whose record is worth seeing.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

NODE_NAME = "project/relocate"


def record_relocate(store: Any, session_id: str, *, project_id: str,
                    old_path: str, new_path: str) -> Optional[str]:
    """Append a ``project/relocate`` node describing ``old → new``.

    Returns the new node id, or ``None`` on any failure — a bookkeeping
    write must never break the repair the user just performed.
    """
    if not session_id or not new_path:
        return None
    try:
        from openprogram.context.nodes import Call, ROLE_CODE
        from openprogram.store import GraphStoreShim

        node = Call(
            id="reloc_" + uuid.uuid4().hex[:10],
            created_at=time.time(),
            role=ROLE_CODE,
            name=NODE_NAME,
            output=f"{old_path or '(unset)'} → {new_path}",
            caller="ROOT",
            predecessor=None,
            metadata={"display": "runtime",
                      "project_id": project_id,
                      "old_path": old_path,
                      "new_path": new_path},
        )
        GraphStoreShim(store, session_id).append(node)
        return node.id
    except Exception:
        return None


__all__ = ["NODE_NAME", "record_relocate"]
