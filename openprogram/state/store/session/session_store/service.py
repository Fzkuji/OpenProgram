"""SessionStore service operations."""
from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

from . import shared
from .index import IndexOperations
from .storage import StorageOperations
from .sessions import SessionsOperations
from .messages import MessagesOperations
from .branches import BranchesOperations
from .node_deletion import NodeDeletionOperations

if TYPE_CHECKING:
    from openprogram.state.store.session.session_log import SessionLog
    from openprogram.state.store.session.memory_index import SessionMemoryIndex


class SessionStore(IndexOperations, StorageOperations, SessionsOperations, MessagesOperations, BranchesOperations, NodeDeletionOperations):
    """Conversations kept in one append-only log per session.

    One instance per process (use ``default_store()``). Methods are
    thread-safe — per-session locks live on the index.

    ``root_path`` is the directory holding the sessions.
    """

    _INDEX_FIELDS = frozenset({
        "id", "agent_id", "title", "created_at", "updated_at",
        "source", "channel", "account_id", "peer_display", "peer_id",
        "pinned", "archived", "group", "status", "unread", "environment_id",
        "sort_at", "sort_since", "agent_trial",
    })

    _EMPTY_SHELL_AGE = 3600       # 1 hour


    def __init__(
        self,
        root_path: shared.Optional[shared.Path] = None,
        *,
        cache_cap: shared.Optional[int] = None,
    ) -> None:
        self.root_path = shared.Path(root_path).expanduser() if root_path else shared._default_root()
        self._explicit_root = root_path is not None
        self._project_ids: dict[str, str] = {}
        self.root_path.mkdir(parents=True, exist_ok=True)
        # Cache: session_id → (SessionLog, SessionMemoryIndex). Lazy,
        # LRU-ordered (OrderedDict: insertion/access order = recency), and
        # size-capped at ``_cache_cap`` — see the _DEFAULT_CACHE_CAP note.
        self._sessions: "OrderedDict[str, tuple[SessionLog, SessionMemoryIndex]]" = shared.OrderedDict()
        self._cache_cap = cache_cap if cache_cap is not None else shared._resolve_cache_cap()
        self._lock = shared.threading.Lock()
        # Filesystem work is serialized per conversation, not across the
        # store, through the process-wide conversation lock (_session_lock).
        self._locations_write_lock = shared.threading.Lock()
        # Location index: session_id → absolute repo path, for sessions
        # that live OUTSIDE the home root (i.e. inside a bound project's
        # ``<project>/.openprogram/sessions/<id>/``). Sessions absent
        # from this index resolve to the home root, exactly as before —
        # so existing installs see zero behaviour change.
        self._locations: dict[str, str] = self._load_locations()
        # Registry: session_id → summary dict. Loaded from index.json at
        # startup, kept in sync by create/update/delete/append_message.
        # list_sessions reads only this — no disk I/O.
        self._index: dict[str, dict[str, shared.Any]] = {}
        self._index_dirty = False
        self._index_timer: shared.Optional[shared.threading.Timer] = None
        self._index_flush_threads: set[shared.threading.Timer] = set()
        self._index_background_enabled = True
        self._index_generation = 0
        self._index_lock = shared.threading.Lock()
        self._index_write_lock = shared.threading.Lock()
        self._load_index()
        shared.atexit.register(self._flush_index)


    def invalidate_cache(self, session_id: str) -> None:
        """Drop the in-memory ``SessionMemoryIndex`` for ``session_id`` so
        the next ``_open`` rebuilds it from disk.

        Needed because Agent method tools run in a spawn()'d
        subprocess (see ``openprogram/runtime/conversation/process_runner.py``) that
        writes Call nodes to the session's log with
        its OWN ``SessionStore`` instance. The parent worker's cached
        index never observes those writes, so ``get_messages`` /
        ``build_branches_payload`` keep returning the pre-subprocess
        snapshot — which is missing every nested code/tool node (an
        Agent method's square + its sub-call and exec children).

        Cheap: the next access reads what was appended.
        """
        with self._session_lock(session_id):
            with self._lock:
                self._sessions.pop(session_id, None)
        shared.report_foreign_change(session_id)


    def mark_merged(self, session_id: str, head_ids: list[str]) -> None:
        """Record that these head ids have been merged into another
        branch — the Branches panel hides them after this.

        The DAG nodes stay intact (a checkout still works) but the
        head no longer surfaces as a standalone branch tip.
        """
        pair = self._open(session_id)
        if pair is None:
            return
        session_log, idx = pair

        def _apply(meta: dict):
            cur = list(meta.get("merged_heads") or [])
            changed = False
            for h in head_ids:
                h = (h or "").strip()
                if h and h not in cur:
                    cur.append(h)
                    changed = True
            meta["merged_heads"] = cur
            return changed, None

        self._update_meta(session_log, idx, _apply, keys=("merged_heads",))


    def merged_heads(self, session_id: str) -> set[str]:
        """Head ids a merge absorbed into another branch.

        ``list_branches`` hides these (their content is reachable from
        the surviving branch), and addressing uses the same set to keep
        a merged head resolving to ITSELF instead of snapping onto
        whichever live branch swallowed it.
        """
        pair = self._open(session_id)
        if pair is None:
            return set()
        _session_log, idx = pair
        merged = set(idx.meta.get("merged_heads") or [])
        # Fallback: auto-detect merged peers by scanning recent
        # ContextCommits for multi-parent commits (= merge commits).
        # Their non-primary parents resolve to head_node_ids that
        # got consumed by the merge. This catches the case where
        # ``mark_merged`` was bypassed by an early-return in
        # ``process_merge_turn`` so the panel still cleans up.
        try:
            from openprogram.state.store.session.context_commit.store import (
                list_commits, load_commit,
            )
            for _c in list_commits(self, session_id, limit=50) or []:
                pids = list(_c.commit_parents or [])
                if len(pids) > 1:
                    for _pid in pids[1:]:
                        try:
                            _peer = load_commit(self, _pid, session_id=session_id)
                        except Exception as e:  # noqa: BLE001 — one bad peer must not
                            # drop the rest of the merge scan.
                            shared._log.debug("merge peer %s unreadable: %s", _pid, e)
                            _peer = None
                        if _peer is not None and _peer.head_node_id:
                            merged.add(_peer.head_node_id)
        except Exception as e:  # noqa: BLE001 — commit subsystem is optional here
            shared._log.debug("merge-head scan skipped for %s: %s", session_id, e)
        return merged


    def list_archived_branches(self, session_id: str) -> list[dict[str, shared.Any]]:
        """Every archived branch of the session, most recently touched
        first, in the same row shape as ``list_branches``.

        Archiving and merging are orthogonal: a merge absorbs a branch
        into another one and drops it from the live tip list, archiving
        records that the agent on it is finished. This view reads the
        ``archived`` flag straight off the branch entries, so a branch
        that was both merged and archived is still listed.
        """
        pair = self._open(session_id)
        if pair is None:
            return []
        _session_log, idx = pair
        rows: list[dict[str, shared.Any]] = []
        # Snapshot: a concurrent turn can add a branch entry mid-scan.
        for head, entry in list((idx.meta.get("branches") or {}).items()):
            if not isinstance(entry, dict) or not entry.get("archived"):
                continue
            if head not in idx.nodes_by_id:
                continue
            rows.append({
                "head_msg_id": head,
                "name": entry.get("name"),
                "created_at": entry.get("created_at"),
                "updated_at": entry.get("updated_at"),
                "archived": True,
            })
        rows.sort(key=lambda r: r.get("updated_at") or 0, reverse=True)
        return rows


    def drop_message(self, session_id: str, node_id: str) -> bool:
        """Remove a single node by id — no descendant walk.

        Used to retire attach-pointer rows that have been consumed by
        a merge: the pointer is a side-channel reference, not part of
        the conv chain, so dropping it doesn't orphan anything. Caller
        is responsible for ``commit_turn`` at the turn boundary.
        """
        pair = self._open(session_id)
        if pair is None:
            return False
        session_log, idx = pair
        if node_id not in idx.nodes_by_id:
            return False
        with idx._lock:
            _removed = idx.nodes_by_id.pop(node_id, None)
            idx.nodes_by_seq = [n for n in idx.nodes_by_seq if n.id != node_id]
            if _removed is not None:
                idx._taken_seqs.discard(_removed.seq)
            for parent, kids in list(idx.children_by_predecessor.items()):
                if node_id in kids:
                    kids.remove(node_id)
                    if not kids:
                        del idx.children_by_predecessor[parent]
            for parent, kids in list(idx.children_by_caller.items()):
                if node_id in kids:
                    kids.remove(node_id)
                    if not kids:
                        del idx.children_by_caller[parent]
        session_log.delete_nodes([node_id])
        self.delete_branch_name(session_id, node_id)
        self._persist_meta(session_log, idx)
        return True


    def get_branch_token_stats(
        self,
        session_id: str,
        head_msg_id: shared.Optional[str] = None,
        *,
        head_id: shared.Optional[str] = None,
        model: shared.Any = None,
    ) -> dict[str, shared.Any]:
        head = head_id or head_msg_id
        chain = self.get_branch(session_id, head) if head else self.get_messages(session_id)
        model_id = getattr(model, "id", None) or (model if isinstance(model, str) else None)

        input_total = output_total = cache_read_total = cache_write_total = 0
        messages_counted = 0
        last_input_tokens = 0
        last_model = None
        for m in chain:
            if m.get("role") != "assistant":
                continue
            if model_id is not None and m.get("token_model") != model_id:
                continue
            i = int(m.get("input_tokens") or 0)
            o = int(m.get("output_tokens") or 0)
            input_total += i
            output_total += o
            cache_read_total += int(m.get("cache_read_tokens") or 0)
            cache_write_total += int(m.get("cache_write_tokens") or 0)
            messages_counted += 1
            if i:
                last_input_tokens = i
            if m.get("token_model"):
                last_model = m["token_model"]
        current_tokens = last_input_tokens + output_total // max(messages_counted, 1)
        # input_tokens excludes the cached and the newly cached tokens, so
        # the prompt is all three.
        denom = input_total + cache_read_total + cache_write_total
        cache_hit_rate = (cache_read_total / denom) if denom else 0.0
        return {
            "input_tokens": input_total, "output_tokens": output_total,
            "cache_read_tokens": cache_read_total, "cache_write_tokens": cache_write_total,
            "cache_read_total": cache_read_total, "cache_hit_rate": cache_hit_rate,
            "messages_counted": messages_counted, "current_tokens": current_tokens,
            "context_window": 0, "pct_used": 0.0,
            "model": last_model or model_id,
        }


    def get_nodes(self, session_id: str) -> list[shared.Call]:
        """Raw Call objects for a session, sorted by seq.

        Lower-level than ``get_messages`` (which returns msg-dict shape)
        — used by code that builds a Graph view (e.g. exec-DAG tree).
        """
        pair = self._open(session_id)
        if pair is None:
            return []
        _session_log, idx = pair
        return idx.all_nodes()


    def latest_user_text(self, session_id: str) -> shared.Optional[str]:
        pair = self._open(session_id)
        if pair is None:
            return None
        _session_log, idx = pair
        for n in reversed(idx.all_nodes()):
            if n.is_user():
                return n.output
        return None


    def sessions_with_binding(self, channel: str, account_id: shared.Optional[str]) -> list[str]:
        out: list[str] = []
        # Registry rows carry channel / account_id as top-level fields.
        for sess in self.list_sessions(limit=10**9, include_archived=True):
            if sess.get("channel") != channel:
                continue
            if account_id is not None and sess.get("account_id") != account_id:
                continue
            out.append(sess["id"])
        return out


    def search_messages(
        self,
        query: str,
        *,
        session_id: shared.Optional[str] = None,
        agent_id: shared.Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, shared.Any]]:
        from ..search import search_messages as _do_search
        return _do_search(
            self, query,
            session_id=session_id, agent_id=agent_id, limit=limit,
        )


    def get_descendants(self, session_id: str, msg_id: str) -> list[dict[str, shared.Any]]:
        pair = self._open(session_id)
        if pair is None:
            return []
        _session_log, idx = pair
        if msg_id not in idx.nodes_by_id:
            return []
        # Descendants follow caller only (sub-calls of this node, not
        # retry siblings). ``idx.descendants`` also crawls predecessor
        # edges, so the caller-only walk is inline.
        result: list[shared.Call] = []
        stack = list(idx.children_by_caller.get(msg_id, []))
        seen: set[str] = set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            node = idx.nodes_by_id.get(cur)
            if node:
                result.append(node)
                stack.extend(idx.children_by_caller.get(cur, []))
        return [shared._node_to_msg(n, session_id) for n in result]


    def get_deepest_leaf(self, session_id: str, msg_id: shared.Optional[str] = None) -> shared.Optional[str]:
        pair = self._open(session_id)
        if pair is None:
            return None
        _session_log, idx = pair
        # Old semantics: longest message-tree chain via the predecessor field.
        hidden = set(idx.meta.get("rewind_hidden_nodes") or [])
        retired = set(idx.meta.get("rewind_hidden_heads") or [])
        children = idx.children_by_predecessor
        roots = [msg_id] if msg_id else [
            n.id for n in idx.all_nodes()
            if not shared._node_conv_predecessor(n)
        ]
        deepest_id: shared.Optional[str] = None
        deepest_depth = -1
        for root in roots:
            if root not in idx.nodes_by_id or root in hidden or root in retired:
                continue
            stack: list[tuple[str, int]] = [(root, 0)]
            while stack:
                cur, depth = stack.pop()
                kids = [child_id for child_id in children.get(cur, [])
                        if child_id not in hidden and not (idx.nodes_by_id[child_id].metadata or {}).get("structural")]
                if not kids:
                    if depth > deepest_depth and cur not in retired:
                        deepest_depth = depth
                        deepest_id = cur
                else:
                    for c in kids:
                        stack.append((c, depth + 1))
        return deepest_id


    def count_recent_nodes(self, since: float) -> int:
        total = 0
        for sess in self.list_sessions(limit=10**9, include_archived=True):
            pair = self._open(sess["id"])
            if not pair:
                continue
            _session_log, idx = pair
            for n in idx.all_nodes():
                if (n.created_at or 0) >= since:
                    total += 1
        return total


    def close(self) -> None:
        """Drain registry writes and stop this store's background timers."""
        with self._index_lock:
            self._index_background_enabled = False
        self._flush_index()
        with self._index_lock:
            # A failed atomic write leaves the cache dirty. Retain the exit
            # retry in that case instead of forgetting unsaved registry data.
            if not self._index_dirty:
                shared.atexit.unregister(self._flush_index)
