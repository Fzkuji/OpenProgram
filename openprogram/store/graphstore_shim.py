"""Backward-compat shim emulating the old ``GraphStore`` API on top of
``SessionStore``.

A handful of call sites (``agentic_programming/runtime.py``,
``agentic_programming/function.py``, ``agent/_turn_lifecycle.py``)
pull a ``GraphStore`` instance out of a ``ContextVar`` and call:

  * ``store.append(node)``      to persist a fresh node
  * ``store.update(node_id, **fields)`` to fill in a placeholder

The new ``SessionStore`` doesn't expose those by id (it deals in
sessions). Rather than rewrite every call site we wrap a
(SessionStore + session_id) pair in this thin shim so the legacy
interface keeps working.

Only ``append`` and ``update`` are emulated — that's all those call
sites use. Anything else raises so we notice quickly if more usage
creeps in.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from openprogram.context.nodes import Call

if TYPE_CHECKING:
    from .session_store import SessionStore


class GraphStoreShim:
    """``GraphStore``-shaped facade backed by ``SessionStore``."""

    def __init__(self, store: "SessionStore", session_id: str):
        self.store = store
        self.session_id = session_id

    def append(self, node: Call) -> None:
        """Persist a Call. Maps to ``SessionStore.append_message`` after
        converting Call → legacy msg dict shape via the existing
        ``_node_to_msg`` helper used elsewhere in the store.

        Note: ``append_message`` is idempotent for known ids, so a
        re-append of the same node id is safe.
        """
        from openprogram.context.session_db import _node_to_msg
        msg = _node_to_msg(node, self.session_id)
        # SessionStore.append_message expects a chat-shaped dict; the
        # _node_to_msg helper returns exactly that.
        self.store.append_message(self.session_id, msg)

    def update(self, node_id: str, **fields: Any) -> None:
        """In-place update of an existing node.

        Used by ``@agentic_function`` exit (fill in output + metadata
        after the function returns) and by error recovery in
        ``_turn_lifecycle.fold_error_into_placeholder``. Updates land
        on both the in-memory index and the on-disk history file
        (rewritten in place — the file is named by seq+role+id, which
        stays the same).
        """
        pair = self.store._open(self.session_id)
        if not pair:
            return
        git, idx = pair
        node = idx.nodes_by_id.get(node_id)
        if node is None:
            return
        for k, v in fields.items():
            if k == "metadata" and isinstance(v, dict):
                node.metadata = {**(node.metadata or {}), **v}
            else:
                setattr(node, k, v)
        # Rewrite the on-disk JSON for this node so a worker restart
        # picks up the new content.
        role_letter = (node.role or "x")[0]
        fname = f"{node.seq:04d}-{role_letter}-{node.id}.json"
        fpath = git.path / "history" / fname
        if fpath.exists():
            tmp = fpath.with_suffix(".json.tmp")
            tmp.write_text(
                __import__("json").dumps(node.to_dict(), ensure_ascii=False, default=str)
            )
            tmp.replace(fpath)
