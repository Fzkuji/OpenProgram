"""SessionStore node deletion: remove single DAG nodes, splice, undo.

Deleting a node removes it and splices whatever followed it onto its
predecessor, the way deleting a box in a diagram leaves its neighbours
in place. The pieces a node owns go with it: everything hung off it by
``caller`` (a reply's tool calls, a call's sub-calls, the agent branch a
call spawned). What merely came after it in the conversation stays and
moves up.

Nothing is unlinked at once. The node records move to a trash folder
outside the session directory together with a journal of every edge, HEAD
and branch-name change, so ``restore_deleted_nodes`` can put the graph
back exactly. Trash older than ``TRASH_TTL`` is purged on the next
delete in the same store.
"""
from __future__ import annotations

import os
import shutil

from . import shared

TRASH_DIR = ".deleted-nodes"
TRASH_TTL = 24 * 3600
_ANY = object()


class NodeDeletionError(ValueError):
    """The request names a node that cannot be deleted."""


class NodeDeletionOperations:
    def _trash_root(self, session_id: str) -> shared.Path:
        return self.root_path / TRASH_DIR / session_id

    def _purge_trash(self) -> None:
        root = self.root_path / TRASH_DIR
        if not root.is_dir():
            return
        cutoff = shared.time.time() - TRASH_TTL
        for sess in root.iterdir():
            if not sess.is_dir():
                continue
            for op in sess.iterdir():
                try:
                    if op.stat().st_mtime < cutoff:
                        shutil.rmtree(op, ignore_errors=True)
                except OSError:
                    pass

    def delete_nodes(self, session_id: str, node_ids: list[str]) -> dict:
        """Delete ``node_ids``, splice their successors, return the journal.

        Raises ``NodeDeletionError`` when a node is missing or is
        structure rather than content: the ROOT, a spawn-branch root (its
        caller call owns it), a compaction summary, or merge/attach
        machinery.
        """
        targets = [n for n in dict.fromkeys(node_ids or []) if n]
        if not targets:
            raise NodeDeletionError("no nodes to delete")
        self._purge_trash()
        with self._session_lock(session_id):
            pair = self._open(session_id)
            if pair is None:
                raise NodeDeletionError(f"unknown session {session_id!r}")
            session_log, idx = pair
            with self._head_file_lock(session_log):
                return self._delete_locked(session_id, session_log, idx, targets)

    def _delete_locked(self, session_id, session_log, idx, targets) -> dict:
        nodes = idx.nodes_by_id
        for nid in targets:
            node = nodes.get(nid)
            if node is None:
                raise NodeDeletionError(f"unknown node {nid!r}")
            md = node.metadata or {}
            if md.get("display") == "root":
                raise NodeDeletionError("the root cannot be deleted")
            if shared._is_spawn_root(md):
                raise NodeDeletionError(
                    f"{nid!r} opens an agent branch; delete the call that spawned it")
            if md.get("covers_ids") is not None:
                raise NodeDeletionError(f"{nid!r} is a compaction summary")
            if md.get("function") in ("attach", "merge"):
                raise NodeDeletionError(f"{nid!r} is {md.get('function')} machinery")

        # Everything a target owns goes with it: its caller subtree, and
        # for each owned node (not the targets themselves) its
        # conversation successors too — an agent branch under a call is
        # that call's work, not the next turn.
        doomed: dict[str, None] = dict.fromkeys(targets)
        stack = list(targets)
        while stack:
            cur = stack.pop()
            kids = list(idx.children_by_caller.get(cur, []))
            if cur not in targets:
                kids += idx.children_by_predecessor.get(cur, [])
            for kid in kids:
                if kid not in doomed and kid in nodes:
                    doomed[kid] = None
                    stack.append(kid)

        def survivor_above(nid):
            """Nearest predecessor of ``nid`` that survives (or None)."""
            seen: set = set()
            cur = nid
            while cur and cur in doomed and cur not in seen:
                seen.add(cur)
                cur = shared._node_conv_predecessor(nodes[cur]) if cur in nodes else None
            return cur if cur not in doomed else None

        reparented = []
        for nid, node in list(nodes.items()):
            if nid in doomed:
                continue
            pred = shared._node_conv_predecessor(node)
            if pred and pred in doomed:
                reparented.append({"id": nid, "old": pred, "new": survivor_above(pred)})

        old_head = idx.head_id
        new_head = survivor_above(old_head) if old_head in doomed else old_head

        # Branch records as they are on disk: the cached copy may miss a
        # write another process made (``_meta_for_write``).
        durable = session_log.read_meta()
        branches = dict(durable.get("branches") or {})
        branch_moves = []
        for nid in doomed:
            if nid not in branches:
                continue
            entry = branches.pop(nid)
            to = survivor_above(nid)
            if to is not None and to not in branches:
                branches[to] = entry
            else:
                to = None
            branch_moves.append({"from": nid, "to": to, "entry": entry})

        op_id = shared.uuid.uuid4().hex[:12]
        trash = self._trash_root(session_id) / op_id
        trash.mkdir(parents=True, exist_ok=True)
        removed_nodes = [nodes[nid].to_dict() for nid in doomed if nid in nodes]

        journal = {
            "op_id": op_id,
            "session_id": session_id,
            "created_at": shared.time.time(),
            "targets": targets,
            "deleted": list(doomed),
            "nodes": removed_nodes,
            "reparented": reparented,
            "head": {"old": old_head, "new": new_head},
            "branch_moves": branch_moves,
        }
        shared.atomic_write_text(
            trash / "journal.json",
            shared.json.dumps(journal, ensure_ascii=False, default=str))

        session_log.delete_nodes(list(doomed))
        for row in reparented:
            session_log.patch_node(row["id"], {"predecessor": row["new"]})

        meta = dict(idx.meta)
        shared.take_durable_branch_records(meta, durable)
        meta["branches"] = branches
        meta["head_id"] = new_head
        self._commit_rebuild(session_log, idx, meta)
        if new_head is None and old_head in doomed:
            tips = self.list_branches(session_id)
            if tips:
                meta = dict(idx.meta)
                shared.take_durable_branch_records(meta, session_log.read_meta())
                meta["head_id"] = tips[0]["head_msg_id"]
                journal["head"]["new"] = meta["head_id"]
                shared.atomic_write_text(
                    trash / "journal.json",
                    shared.json.dumps(journal, ensure_ascii=False, default=str))
                self._commit_rebuild(session_log, idx, meta)
        return journal

    def restore_deleted_nodes(self, session_id: str, op_id: str) -> dict:
        """Undo ``delete_nodes`` op ``op_id``; returns its journal.

        Edges and HEAD are restored only where nothing has moved them
        since: a successor that was re-pointed again, or a HEAD the user
        moved on from, keeps its newer value.
        """
        if not op_id or "/" in op_id or op_id.startswith("."):
            raise NodeDeletionError(f"unknown delete {op_id!r}")
        trash = self._trash_root(session_id) / op_id
        jpath = trash / "journal.json"
        if not jpath.is_file():
            raise NodeDeletionError(f"unknown delete {op_id!r}")
        journal = shared.json.loads(jpath.read_text(encoding="utf-8"))
        with self._session_lock(session_id):
            pair = self._open(session_id)
            if pair is None:
                raise NodeDeletionError(f"unknown session {session_id!r}")
            session_log, idx = pair
            with self._head_file_lock(session_log):
                for payload in journal.get("nodes") or []:
                    if payload.get("id") not in idx.nodes_by_id:
                        session_log.write_history(payload.get("seq", -1), payload.get("role", ""),
                                          payload.get("id"), payload)
                for row in journal.get("reparented") or []:
                    node = idx.nodes_by_id.get(row["id"])
                    if node is not None and node.predecessor == row["new"]:
                        session_log.patch_node(row["id"], {"predecessor": row["old"]})
                meta = dict(idx.meta)
                meta["head_id"] = idx.head_id
                head = journal.get("head") or {}
                if meta["head_id"] == head.get("new"):
                    meta["head_id"] = head.get("old")
                durable = session_log.read_meta()
                shared.take_durable_branch_records(meta, durable)
                branches = dict(durable.get("branches") or {})
                for move in journal.get("branch_moves") or []:
                    to = move.get("to")
                    if to and branches.get(to) == move.get("entry"):
                        branches.pop(to)
                    branches.setdefault(move["from"], move.get("entry"))
                meta["branches"] = branches
                self._commit_rebuild(session_log, idx, meta)
        shutil.rmtree(trash, ignore_errors=True)
        return journal

    def _commit_rebuild(self, session_log, idx, meta: dict) -> None:
        """Write ``meta`` and rebuild the index from the log."""
        with idx._persist_lock:
            session_log.write_meta(meta)
            idx.rebuild(
                session_log.list_history(), meta,
                shared._node_conv_predecessor, shared._node_caller,
            )
            session_log.mark_synced()

