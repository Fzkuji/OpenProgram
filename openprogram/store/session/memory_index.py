"""Per-session in-memory DAG index.

Loaded from a session's git repo at startup / first access; rebuilt
on demand if dropped. Holds:

  * ``nodes_by_id`` — id → Call lookup. O(1) any read by id.
  * ``nodes_by_seq`` — ordered list. ``get_messages()`` returns this.
  * ``children_by_predecessor`` — predecessor → [child_id]. For "find
    conv children of this user/assistant turn". Stable insertion
    order matches seq order so retry sibling ordering is preserved.
  * ``children_by_caller`` — caller_id → [callee_id]. For "find tool
    sub-calls of this assistant".
  * ``head_id`` — current head pointer (mirror of meta.json).
  * ``meta`` — session-level dict (title, agent_id, created_at, ...).
  * ``next_seq`` — monotonic counter, hands out the next seq for
    ``append``.

Git is the truth-of-storage; this object is a query cache. ``rebuild``
walks ``history/`` and reads ``meta.json`` to repopulate fully.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from openprogram.context.nodes import Call


@dataclass
class SessionMemoryIndex:
    """Query index for one session. All operations are in-memory. The
    GitSession (this index's sibling) handles disk persistence; we
    only read disk inside ``rebuild_from_git``.
    """
    nodes_by_id: dict[str, Call] = field(default_factory=dict)
    nodes_by_seq: list[Call] = field(default_factory=list)
    children_by_predecessor: dict[str, list[str]] = field(default_factory=dict)
    children_by_caller: dict[str, list[str]] = field(default_factory=dict)
    head_id: Optional[str] = None
    meta: dict = field(default_factory=dict)
    next_seq: int = 0
    # Every seq currently held by a node. Guards against handing out a
    # seq an abandoned node already owns (see ``append``); a set instead
    # of scanning nodes_by_seq keeps append O(1) on long sessions.
    _taken_seqs: set[int] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _persist_lock: threading.Lock = field(default_factory=threading.Lock)

    # Mutations

    def append(self, node: Call, *, predecessor: Optional[str], caller: Optional[str]) -> int:
        """Insert a fresh node. Assigns seq if unset. Returns assigned seq.

        ``predecessor`` / ``caller`` are passed explicitly because the
        Call dataclass doesn't track the conv edge separately — it
        carries ``caller`` but the conv edge is a metadata-level
        concept (see DagSessionDB._msg_to_node mapping). Keep them as
        explicit args so the writer is the single source of truth.
        """
        with self._lock:
            if node.seq < 0:
                # Skip any seq already taken. A turn abandoned mid-stream
                # (crash / lost worker) leaves its llm node on disk holding
                # a seq while the counter can come back lower after a
                # rebuild, and the next user message would then collide
                # with it — two nodes at the same seq, one history filename
                # each, ordering undefined. Advancing past occupied seqs
                # costs nothing and makes a collision unrepresentable.
                while self.next_seq in self._taken_seqs:
                    self.next_seq += 1
                node.seq = self.next_seq
                self.next_seq += 1
            else:
                # Caller pre-assigned (e.g. rebuilding from disk).
                # Push counter past it so future appends don't collide.
                if node.seq >= self.next_seq:
                    self.next_seq = node.seq + 1
            self.nodes_by_id[node.id] = node
            self.nodes_by_seq.append(node)
            self._taken_seqs.add(node.seq)
            if predecessor:
                self.children_by_predecessor.setdefault(predecessor, []).append(node.id)
            if caller:
                self.children_by_caller.setdefault(caller, []).append(node.id)
            return node.seq

    def set_head(self, head_id: Optional[str]) -> None:
        with self._lock:
            self.head_id = head_id

    def set_meta(self, **fields: Any) -> None:
        with self._lock:
            self.meta.update(fields)

    def reindex_edges(
        self,
        node_id: str,
        *,
        old_predecessor: Optional[str],
        old_caller: Optional[str],
    ) -> None:
        """Synchronize forward indexes after an existing node is reparented."""
        with self._lock:
            node = self.nodes_by_id.get(node_id)
            if node is None:
                return

            def _move(index, old: Optional[str], new: Optional[str]) -> None:
                if old == new:
                    return
                if old:
                    siblings = index.get(old, [])
                    if node_id in siblings:
                        siblings.remove(node_id)
                    if not siblings:
                        index.pop(old, None)
                if new:
                    children = index.setdefault(new, [])
                    if node_id not in children:
                        children.append(node_id)

            _move(
                self.children_by_predecessor,
                old_predecessor,
                node.predecessor or None,
            )
            _move(self.children_by_caller, old_caller, node.caller or None)

    def update_branch_entry(self, head_msg_id: str, mutate) -> dict:
        """Merge one branch's ``meta["branches"]`` entry and return it.

        ``mutate(entry)`` gets the current entry and sets only the keys
        it owns. Read, merge and write happen inside the lock, so a
        writer that sets the name cannot drop a key another thread wrote
        in between — the Stage-2 auto-namer runs on a background thread
        and would otherwise write back a branches dict it read before
        ``archive_agent`` set ``archived`` (agent-collaboration.md §2.6).
        """
        with self._lock:
            branches = dict(self.meta.get("branches") or {})
            entry = dict(branches.get(head_msg_id) or {})
            mutate(entry)
            branches[head_msg_id] = entry
            self.meta["branches"] = branches
            return dict(entry)

    # Queries

    def get(self, node_id: str) -> Optional[Call]:
        return self.nodes_by_id.get(node_id)

    def get_branch(self, head_id: Optional[str], get_edge) -> list[Call]:
        """Walk back from ``head_id`` via the conv edge, return root→head.

        ``get_edge(call) -> Optional[str]`` resolves the parent for a
        given Call. The store passes a closure that reads the right
        edge (``_node_conv_predecessor``), so this class stays
        ignorant of which field holds it and works on raw payload
        dicts as well as ``Call`` objects.
        """
        chain: list[Call] = []
        cur: Optional[str] = head_id or self.head_id
        seen: set[str] = set()
        while cur is not None and cur in self.nodes_by_id and cur not in seen:
            seen.add(cur)
            node = self.nodes_by_id[cur]
            chain.append(node)
            cur = get_edge(node)
        chain.reverse()
        return chain

    def all_nodes(self) -> list[Call]:
        """Every node, sorted by seq. O(N), N = nodes in session."""
        return list(self.nodes_by_seq)

    def descendants(self, root_id: str, *, follow_caller: bool = False) -> list[Call]:
        """BFS from ``root_id`` via ``children_by_predecessor`` (and
        optionally ``children_by_caller``). Used by
        ``DagSessionDB.get_descendants`` and ``delete_branch_tail``.
        """
        out: list[Call] = []
        stack = [root_id]
        seen: set[str] = set()
        while stack:
            cur = stack.pop(0)
            if cur in seen:
                continue
            seen.add(cur)
            for cid in self.children_by_predecessor.get(cur, []):
                if cid in self.nodes_by_id:
                    out.append(self.nodes_by_id[cid])
                    stack.append(cid)
            if follow_caller:
                for cid in self.children_by_caller.get(cur, []):
                    if cid in self.nodes_by_id:
                        out.append(self.nodes_by_id[cid])
                        stack.append(cid)
        return out

    # Rebuild

    def reset(self) -> None:
        with self._lock:
            self.nodes_by_id.clear()
            self.nodes_by_seq.clear()
            self.children_by_predecessor.clear()
            self.children_by_caller.clear()
            self.head_id = None
            self.meta.clear()
            self._taken_seqs.clear()
            self.next_seq = 0

    def rebuild_from_paths(
        self,
        history_files: list,
        meta: dict,
        get_predecessor,
        get_caller,
    ) -> None:
        """Walk a list of ``history/*.json`` paths in seq order and rebuild.

        ``get_predecessor(payload) -> Optional[str]`` and
        ``get_caller(payload) -> Optional[str]`` translate the raw
        JSON dict back to edge ids. They live outside this class
        because the field layout (``predecessor`` vs
        ``caller``) belongs to the message-dict adapter, not the
        index.
        """
        self.reset()
        for fpath in history_files:
            try:
                payload = json.loads(fpath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            # Drop fields not on the Call dataclass; tolerate extras.
            kwargs = {k: payload.get(k) for k in (
                "id", "created_at", "seq", "role", "name",
                "input", "output", "caller", "predecessor", "reads",
                "metadata",
            )}
            kwargs = {k: v for k, v in kwargs.items() if v is not None}
            try:
                node = Call(**kwargs)
            except TypeError:
                continue
            self.append(
                node,
                predecessor=get_predecessor(payload),
                caller=get_caller(payload),
            )
        self.meta = dict(meta or {})
        self.head_id = self.meta.get("head_id") or self.meta.get("last_node_id")
