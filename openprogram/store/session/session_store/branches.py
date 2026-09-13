"""SessionStore branches operations."""
from __future__ import annotations
from . import shared


class BranchesOperations:
    def get_branch(
        self,
        session_id: str,
        head_msg_id: shared.Optional[str] = None,
    ) -> list[dict[str, shared.Any]]:
        pair = self._open(session_id)
        if pair is None:
            return []
        _git, idx = pair
        head = head_msg_id or idx.head_id
        if not head or head not in idx.nodes_by_id:
            return []

        # Pure predecessor-edge walk (dag/overview.md). A
        # node without a predecessor must be a legal branch terminus
        # (spawn root / ROOT / session first node) — anything else is
        # broken data and raises. No caller/seq heuristics.
        def _edge(node):
            pred = shared._node_conv_predecessor(node)
            if pred:
                return pred
            meta = node.metadata or {}
            if shared._is_spawn_root(meta):
                return None          # spawn branch root — legal stop
            if meta.get("display") == "root":
                return None          # the ROOT node itself
            if node.role not in (shared.ROLE_USER, shared.ROLE_LLM):
                return None          # code node opening a run branch
            if shared._is_first_conv_node(idx, node):
                return None          # session first node — legal stop
            if meta.get("covers_ids") is not None:
                # Compaction summary covering from the very start of the
                # session: it inherits the first node's empty predecessor
                # and becomes the new chain terminus — the same exemption
                # the write invariant grants (§8 above).
                return None
            raise shared.BrokenPredecessorChainError(session_id, node.id)

        chain = idx.get_branch(head, _edge)
        return [
            shared._node_to_msg(n, session_id) for n in chain
            if (n.metadata or {}).get("display") != "root"
            and not (n.metadata or {}).get("rewound")
        ]


    def spawn_branch(
        self,
        session_id: str,
        caller_node_id: str,
        *,
        source: str,
        name: shared.Optional[str] = None,
        node_id: shared.Optional[str] = None,
        prompt: str = "",
        created_at: shared.Optional[float] = None,
        metadata: shared.Optional[dict] = None,
        register_head: bool = True,
    ) -> str:
        """Open a spawn branch: create the branch-root user node
        (``predecessor=None``, ``caller=caller_node_id``,
        ``metadata.source=source``) and return the branch-root id. The
        ONLY sanctioned way to open a spawn branch — call sites never
        hand-assemble the edge.

        ``register_head=False`` (same-session sub-agent spawns): the
        branch opens WITHOUT stealing the session head — the user's
        conversation stays the active branch while the agent runs
        (context/compaction.md §5, HEAD single-writer).
        """
        from ..session_node_writer import SessionNodeWriter

        meta = dict(metadata or {})
        meta["source"] = source
        meta["spawn_branch_root"] = True
        meta.pop("predecessor", None)
        node = shared.Call(
            id=node_id or shared.uuid.uuid4().hex[:12],
            created_at=created_at or shared.time.time(),
            role=shared.ROLE_USER,
            output=prompt,
            caller=caller_node_id or "ROOT",
            predecessor=None,
            metadata=meta,
        )
        SessionNodeWriter(self, session_id).append(node)
        if register_head:
            # Register the branch head so mid-run loads resolve onto
            # the new branch (shim skips set_head for caller-tagged
            # nodes).
            self.set_head(session_id, node.id)
        if name:
            try:
                self.set_branch_name(session_id, node.id, name)
            except (OSError, ValueError, KeyError) as e:
                shared._log.warning("branch name %r NOT recorded for %s: %s",
                             name, node.id, e)
        return node.id


    def set_head(self, session_id: str, head_id: shared.Optional[str]) -> None:
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        git, idx = pair
        # A summary node is a stand-in, never a conversational tip: a
        # head on it makes the active branch [summary] alone and hides
        # the whole session (context/compaction.md §5). No caller has a
        # legitimate reason; refuse loudly instead of storing it.
        node = idx.nodes_by_id.get(head_id) if head_id else None
        if node is not None and (node.metadata or {}).get("covers_ids"):
            raise ValueError(
                f"set_head: {head_id!r} is a compaction summary — "
                "a stand-in cannot be the active branch tip"
            )
        idx.set_head(head_id)
        self._persist_meta(git, idx)


    def compare_and_set_head(
        self,
        session_id: str,
        expected_head_id: shared.Optional[str],
        new_head_id: shared.Optional[str],
        *,
        branch_update: shared.Optional[dict[str, shared.Any]] = None,
        meta_update: shared.Optional[dict[str, shared.Any]] = None,
    ) -> bool:
        """Durable cross-process HEAD CAS, optionally activating a branch ref."""
        with self._session_lock(session_id):
            pair = self._open(session_id)
            if pair is None:
                return False
            git, idx = pair
            node = idx.nodes_by_id.get(new_head_id) if new_head_id else None
            if node is not None and (node.metadata or {}).get("covers_ids"):
                raise ValueError(
                    f"compare_and_set_head: {new_head_id!r} is a compaction summary"
                )
            with self._head_file_lock(git):
                try:
                    durable = git.read_meta()
                    if durable.get("head_id") != expected_head_id:
                        return False
                    updated = dict(durable)
                    updated["head_id"] = new_head_id
                    updated["head_version"] = int(
                        durable.get("head_version") or 0
                    ) + 1
                    if branch_update:
                        refs = dict(durable.get("branch_refs") or {})
                        source_id = branch_update.get("source_branch_id")
                        target_id = branch_update.get("target_branch_id")
                        if source_id:
                            source = dict(refs.get(source_id) or {})
                            source.setdefault("branch_id", source_id)
                            source.setdefault("head_id", expected_head_id)
                            source.setdefault("head_version", int(
                                durable.get("head_version") or 0
                            ))
                            source.setdefault("writer_epoch", int(
                                durable.get("writer_epoch") or 0
                            ))
                            refs[source_id] = source
                        if target_id and not branch_update.get("preserve_target"):
                            target = dict(refs.get(target_id) or {})
                            target.update({
                                "branch_id": target_id,
                                "head_id": new_head_id,
                                "parent_branch_id": source_id,
                                "head_version": updated["head_version"],
                                "writer_epoch": int(
                                    durable.get("writer_epoch") or 0
                                ) + 1,
                                "status": branch_update.get("target_status", "active"),
                            })
                            refs[target_id] = target
                        elif target_id and target_id in refs \
                                and branch_update.get("target_status"):
                            target = dict(refs[target_id])
                            target["status"] = branch_update["target_status"]
                            refs[target_id] = target
                        updated["branch_refs"] = refs
                        active_id = branch_update.get("active_branch_id")
                        if active_id:
                            updated["active_branch_id"] = active_id
                        updated["writer_epoch"] = int(
                            durable.get("writer_epoch") or 0
                        ) + 1
                    if meta_update:
                        updated.update(meta_update)
                    git.write_meta(updated)
                    with idx._persist_lock:
                        with idx._lock:
                            idx.head_id = new_head_id
                            idx.meta = updated
                    return True
                finally:
                    pass


    def message_exists(self, session_id: str, msg_id: str) -> bool:
        pair = self._open(session_id)
        if pair is None:
            return False
        _git, idx = pair
        return msg_id in idx.nodes_by_id


    def has_persisted_ancestor(
        self, session_id: str, ancestor_id: str, descendant_id: str,
    ) -> bool:
        """Read the on-disk predecessor chain, without trusting cached nodes."""
        pair = self._open(session_id)
        if pair is None or not ancestor_id or not descendant_id:
            return False
        git, _idx = pair
        paths = {}
        for path in git.list_history():
            parts = path.stem.split("-", 2)
            if len(parts) == 3:
                node_id = parts[2]
                if node_id in paths:
                    return False
                paths[node_id] = path
        visited = set()
        current = descendant_id
        while current and current not in visited:
            visited.add(current)
            path = paths.get(current)
            if path is None:
                return False
            try:
                node = shared.json.loads(shared.read_text_with_retry(path))
            except (OSError, shared.json.JSONDecodeError):
                return False
            if not isinstance(node, dict) or node.get("id") != current:
                return False
            if current == ancestor_id:
                return True
            current = node.get("predecessor")
            if not isinstance(current, str):
                return False
        return False


    def list_branches(self, session_id: str) -> list[dict[str, shared.Any]]:
        pair = self._open(session_id)
        if pair is None:
            return []
        _git, idx = pair
        # A branch tip is a conv node (no caller) with no conv-child.
        tips: list[dict[str, shared.Any]] = []
        named = (idx.meta.get("branches") or {})

        def _top_program_run(node: shared.Call) -> bool:
            """A caller-less Program is a conversation-layer action."""
            md = node.metadata or {}
            return (
                node.role == shared.ROLE_CODE
                and (shared._node_caller(node) or "ROOT") == "ROOT"
                and bool(node.name or md.get("function"))
            )

        def _conversation_node(child: shared.Call) -> bool:
            """Whether a node participates in the predecessor conversation."""
            md = child.metadata or {}
            if md.get("display") in ("root", "runtime"):
                return False
            if md.get("function") == "attach":
                return False
            if str(child.name or "").startswith("context/"):
                return False
            if _top_program_run(child):
                return True
            if child.role not in (shared.ROLE_USER, shared.ROLE_LLM):
                return False
            caller = shared._node_caller(child)
            if caller and caller != "ROOT":
                caller_node = idx.nodes_by_id.get(caller)
                if caller_node is not None and caller_node.role not in (
                    shared.ROLE_USER, shared.ROLE_LLM,
                ):
                    return False
            return True

        def _conv_child(kid_id: str) -> bool:
            """Whether this predecessor child continues the conversation."""
            child = idx.nodes_by_id.get(kid_id)
            return child is not None and _conversation_node(child)

        merged = self.merged_heads(session_id)

        for node in idx.all_nodes():
            if not _conversation_node(node):
                continue
            kids = idx.children_by_predecessor.get(node.id, [])
            if any(_conv_child(k) for k in kids):
                continue
            # Heads that a merge consumed don't surface as standalone
            # branches anymore — their content lives on the merge tip.
            if node.id in merged:
                continue
            label = named.get(node.id)
            name = label.get("name") if isinstance(label, dict) else label
            # No "main" special-case: the trunk tip is named exactly like
            # any other branch — its own name, or None (→ id short-hex in
            # the badge). The trunk identity is separate from the name.
            # See branch-naming.md 决策 3.
            tips.append({
                "head_msg_id": node.id,
                "name": name,
                "created_at": (label or {}).get("created_at") if isinstance(label, dict) else node.created_at,
                "updated_at": (label or {}).get("updated_at") if isinstance(label, dict) else node.created_at,
                "archived": bool(label.get("archived")) if isinstance(label, dict) else False,
            })
        # Compaction no longer clones the kept tail (§8): a summary node
        # splices into the chain and the tail keeps its own ids, so the
        # branch tips need no translation. A summary node that ends up a
        # tip is still machinery, not a checkout target.
        tips = [t for t in tips
                if not str(t["head_msg_id"]).startswith("summary_")]
        tips.sort(key=lambda r: r.get("updated_at") or 0, reverse=True)
        return tips


    def set_branch_name(
        self,
        session_id: str,
        head_msg_id: str,
        name: str,
        **fields: shared.Any,
    ) -> None:
        """Set a branch's name, merging (not replacing) its meta entry.

        ``**fields`` writes auto-naming state alongside the name
        (``auto_named`` / ``name_locked`` / ``name_gen_count`` / ``turns``;
        see docs/design/runtime/branch-naming.md). Unspecified existing
        fields are preserved — callers that only touch the name must not
        wipe the lock, the counters, or the archive flag."""
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        git, idx = pair

        def _mutate(entry: dict) -> None:
            now = shared.time.time()
            entry["name"] = name
            entry.setdefault("created_at", now)
            entry["updated_at"] = now
            entry.update(fields)

        idx.update_branch_entry(head_msg_id, _mutate)
        self._persist_meta(git, idx)


    def get_branch_meta(self, session_id: str, head_msg_id: str) -> dict[str, shared.Any]:
        """Return a branch's full meta entry (name + auto-naming state),
        or ``{}`` if the branch has no entry. Used by the auto-namer to
        re-read the lock before writing back (see branch-naming.md
        "优先级与锁")."""
        pair = self._open(session_id)
        if pair is None:
            return {}
        _git, idx = pair
        return dict((idx.meta.get("branches") or {}).get(head_msg_id) or {})


    def set_branch_meta(
        self, session_id: str, head_msg_id: str, **fields: shared.Any,
    ) -> None:
        """Merge ``fields`` into a branch's meta entry without touching
        its name. Used for lifecycle facts that ride the same entry as
        the name (``archived`` / ``archived_at`` / ``archived_reason``
        — see agent-collaboration.md, archiving)."""
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        git, idx = pair

        def _mutate(entry: dict) -> None:
            now = shared.time.time()
            entry.update(fields)
            entry.setdefault("created_at", now)
            entry["updated_at"] = now

        idx.update_branch_entry(head_msg_id, _mutate)
        self._persist_meta(git, idx)


    def bump_branch_turns(self, session_id: str, head_msg_id: str) -> int:
        """Increment a branch's per-branch turn counter and return the
        new value. Used by finalize_turn to decide whether to trigger
        Stage-2 auto-rename (counter, not a message count — see
        branch-naming.md 第四节)."""
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return 0
        git, idx = pair

        def _mutate(entry: dict) -> None:
            entry["turns"] = int(entry.get("turns", 0)) + 1

        merged = idx.update_branch_entry(head_msg_id, _mutate)
        self._persist_meta(git, idx)
        return int(merged.get("turns", 0))


    def delete_branch_name(self, session_id: str, head_msg_id: str) -> None:
        pair = self._open(session_id)
        if pair is None:
            return
        git, idx = pair
        branches = dict(idx.meta.get("branches") or {})
        if branches.pop(head_msg_id, None) is None:
            return
        idx.set_meta(branches=branches)
        self._persist_meta(git, idx)


    def delete_branch_tail(self, session_id: str, head_msg_id: str) -> int:
        pair = self._open(session_id)
        if pair is None:
            return 0
        git, idx = pair
        if head_msg_id not in idx.nodes_by_id:
            return 0
        # Fork point the doomed subtree hangs off — the head fallback
        # if the session head turns out to live inside the subtree.
        root_pred = shared._node_conv_predecessor(idx.nodes_by_id[head_msg_id])
        # Collect head + descendants (both conv-children and sub-calls).
        to_delete: list[str] = [head_msg_id]
        seen: set[str] = {head_msg_id}
        stack: list[str] = [head_msg_id]
        while stack:
            cur = stack.pop()
            for cid in idx.children_by_predecessor.get(cur, []):
                if cid not in seen:
                    seen.add(cid)
                    to_delete.append(cid)
                    stack.append(cid)
            for cid in idx.children_by_caller.get(cur, []):
                if cid not in seen:
                    seen.add(cid)
                    to_delete.append(cid)
                    stack.append(cid)

        # Drop from index + remove history files. ID set kept so the
        # rest of the index stays consistent.
        with idx._lock:
            for nid in to_delete:
                node = idx.nodes_by_id.pop(nid, None)
                if node is None:
                    continue
                # Remove from sorted list (linear scan; tiny lists in practice)
                idx.nodes_by_seq = [n for n in idx.nodes_by_seq if n.id != nid]
                # Free the seq so append can reuse it (the collision guard
                # in SessionMemoryIndex.append reads this set).
                idx._taken_seqs.discard(node.seq)
                # Detach from children indices
                for parent, kids in list(idx.children_by_predecessor.items()):
                    if nid in kids:
                        kids.remove(nid)
                        if not kids:
                            del idx.children_by_predecessor[parent]
                for parent, kids in list(idx.children_by_caller.items()):
                    if nid in kids:
                        kids.remove(nid)
                        if not kids:
                            del idx.children_by_caller[parent]
                # File removal
                for fpath in (git.path / "history").glob(f"*-{nid}.json"):
                    try:
                        fpath.unlink()
                    except OSError:
                        pass
        # Drop named branches for the deleted heads
        branches = dict(idx.meta.get("branches") or {})
        for nid in to_delete:
            branches.pop(nid, None)
        idx.set_meta(branches=branches)
        # A head inside the deleted subtree must not survive the
        # deletion — a dangling head renders the session empty. Fall
        # back to the fork point the subtree hung off (None = the tail
        # was the whole session). Callers that pick a smarter new head
        # (the ws handler moves to another named leaf) overwrite this.
        if idx.head_id in seen:
            idx.set_head(root_pred)
        self._persist_meta(git, idx)
        return len(to_delete)

