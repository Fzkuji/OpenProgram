"""Node-level write handle onto one session's DAG.

The dispatcher binds a ``SessionNodeWriter`` into the ``_store``
ContextVar for the duration of a turn, so deep code
(``agentic_programming/runtime.py``, ``agentic_programming/function.py``,
``agent/internals/_turn_lifecycle.py``) writes DAG nodes without
threading ``(store, session_id)`` through every layer:

  * ``append(node)``               persist a fresh Call
  * ``update(node_id, **fields)``  fill in a placeholder in place
  * ``load()``                     the session's DAG as a graph

``SessionStore`` itself deals in whole sessions and message dicts; this
is the by-node seam onto the same repo. It also carries the HEAD
single-writer rule (context/compaction.md §5): ``advance_head=False``
lets a spawned same-session turn write its nodes without stealing the
session head.

Node writes go through here and nowhere else — anything not listed
above raises, so a new write path is noticed immediately.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from openprogram.context.nodes import Call

if TYPE_CHECKING:
    from .session_store import SessionStore


class SessionNodeWriter:
    """Per-node write handle onto one session, backed by ``SessionStore``."""

    def __init__(self, store: "SessionStore", session_id: str,
                 *, advance_head: bool = True):
        self.store = store
        self.session_id = session_id
        # HEAD single-writer (context/compaction.md §5): a spawned
        # sub-agent turn writes through a shim with advance_head=False
        # so its conversation nodes never steal the session head.
        self.advance_head = advance_head

    def append(self, node: Call, *, create_if_missing: bool = True) -> None:
        """Persist a Call directly into the per-session index + history.

        Writes the Call as-is (no lossy chat-msg round trip), assigns
        a seq, and bumps head for conversation nodes. Idempotent on id.
        With create_if_missing=False, a removed session is never initialized.
        Replaying an existing ID completes any interrupted history write.
        """
        import time as _time
        pair = self.store._open(self.session_id, create_if_missing=create_if_missing)
        if pair is None:
            return
        git, idx = pair
        if node.id in idx.nodes_by_id:
            # A previous append may have updated the index before its history
            # write failed. Replaying the same ID must complete persistence.
            existing = idx.nodes_by_id[node.id]
            git.write_history(existing.seq, existing.role, existing.id, existing.to_dict())
            return
        meta = node.metadata or {}
        # The conv edge lives ONLY on the top-level field.
        predecessor = node.predecessor or ""
        caller = node.caller or meta.get("caller") or ""
        from .session_store import _check_append_invariant
        _check_append_invariant(self.session_id, idx, node, predecessor, caller)
        seq = idx.append(node, predecessor=predecessor, caller=caller)
        self.store.spill_large_node(self.session_id, node)
        git.write_history(seq, node.role, node.id, node.to_dict())
        if not caller and self.advance_head:
            idx.set_head(node.id)
        activity_at = _time.time()
        idx.set_meta(updated_at=activity_at)
        # Persist the activity time even when this node does not advance
        # HEAD; otherwise meta.json and the sidebar registry disagree.
        self.store._persist_meta(git, idx)
        # Registry too — same as SessionStore.append_message: updated_at
        # is the sidebar's recency-sort key. The webui dispatcher appends
        # through THIS shim, so skipping the registry here left every web
        # chat row without a timestamp and the sidebar order collapsed to
        # insertion order after a refresh (new sessions sank to the bottom).
        fields: dict = {"updated_at": activity_at}
        if node.role == "user" and node.output:
            text = str(node.output or "").strip().replace("\n", " ")
            fields["preview"] = (text[:77] + "…") if len(text) > 80 else text
        self.store._update_index_entry(self.session_id, **fields)
        self.store._schedule_index_flush()

    def load(self):
        """Return a ``Graph`` populated with all nodes for this session.

        Legacy ``GraphStore.load()`` semantics: ``Graph.nodes`` is a
        dict ``id → Call`` and ``Graph._next_seq`` is one past the
        highest seq present. Test code reads ``g.nodes`` to assert
        node fields.
        """
        import copy
        from openprogram.context.nodes import Graph
        g = Graph()
        pair = self.store._open(self.session_id)
        if not pair:
            return g
        _git, idx = pair
        # Deep-copy so callers see a point-in-time copy; the live
        # index keeps mutating as @agentic_function fills placeholders.
        for node in idx.nodes_by_seq:
            frozen = copy.deepcopy(node)
            g.nodes[frozen.id] = frozen
            if frozen.seq >= g._next_seq:
                g._next_seq = frozen.seq + 1
        return g

    def update(self, node_id: str, **fields: Any) -> None:
        """In-place update of an existing node.

        Used by ``@agentic_function`` exit (fill in output + metadata
        after the function returns) and by error recovery in
        ``_turn_lifecycle.fold_error_into_placeholder``. Updates land
        on both the in-memory index and the on-disk history file
        (rewritten in place — the file is named by seq+role+id, which
        stays the same).
        """
        self.store.update_node(self.session_id, node_id, **fields)
