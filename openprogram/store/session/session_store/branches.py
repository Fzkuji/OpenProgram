"""SessionStore branches operations."""
from __future__ import annotations
from . import shared
from ..placement import validate_session_id


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
        def transform(meta, idx):
            node = idx.nodes_by_id.get(head_id) if head_id else None
            if node is not None and (node.metadata or {}).get("covers_ids"):
                raise ValueError(
                    f"set_head: {head_id!r} is a compaction summary — "
                    "a stand-in cannot be the active branch tip"
                )
            meta["head_id"] = head_id
            meta.pop("last_node_id", None)
            return meta

        self._transform_session_meta(session_id, transform)


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
        validate_session_id(session_id)

        def transform(durable, idx):
            node = idx.nodes_by_id.get(new_head_id) if new_head_id else None
            if node is not None and (node.metadata or {}).get("covers_ids"):
                raise ValueError(
                    f"compare_and_set_head: {new_head_id!r} is a compaction summary"
                )
            current_head = durable.get("head_id") or durable.get("last_node_id")
            if current_head != expected_head_id:
                return None
            reserved = {
                "head_id", "last_node_id", "head_version", "writer_epoch",
                "branch_refs", "active_branch_id",
            }.intersection(meta_update or {})
            if reserved:
                raise ValueError(
                    "meta_update cannot replace HEAD control fields: "
                    + ", ".join(sorted(reserved))
                )
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
            updated.pop("last_node_id", None)
            return shared.json.loads(shared.json.dumps(updated, ensure_ascii=False, default=str))

        return self._transform_session_meta(
            session_id, transform, create_if_missing=False,
        ) is not None


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


    def _update_branch_metadata(self, session_id, head_msg_id, mutate):
        def update(branches):
            entry = dict(branches.get(head_msg_id) or {})
            mutate(entry)
            branches[head_msg_id] = entry
            return branches

        branches = self.update_session_dict(session_id, "branches", update)
        return {} if branches is None else dict(branches[head_msg_id])


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
        def _mutate(entry: dict) -> None:
            now = shared.time.time()
            entry["name"] = name
            entry.setdefault("created_at", now)
            entry["updated_at"] = now
            entry.update(fields)

        self._update_branch_metadata(session_id, head_msg_id, _mutate)


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
        def _mutate(entry: dict) -> None:
            now = shared.time.time()
            entry.update(fields)
            entry.setdefault("created_at", now)
            entry["updated_at"] = now

        self._update_branch_metadata(session_id, head_msg_id, _mutate)


    def bump_branch_turns(self, session_id: str, head_msg_id: str) -> int:
        """Increment a branch's per-branch turn counter and return the
        new value. Used by finalize_turn to decide whether to trigger
        Stage-2 auto-rename (counter, not a message count — see
        branch-naming.md 第四节)."""
        def _mutate(entry: dict) -> None:
            entry["turns"] = int(entry.get("turns", 0)) + 1

        merged = self._update_branch_metadata(session_id, head_msg_id, _mutate)
        return int(merged.get("turns", 0))


    def delete_branch_name(self, session_id: str, head_msg_id: str) -> None:
        def transform(meta, _idx):
            branches = dict(meta.get("branches") or {})
            if branches.pop(head_msg_id, None) is None:
                return None
            meta["branches"] = branches
            return meta

        self._transform_session_meta(session_id, transform, create_if_missing=False)


    def delete_branch_tail(self, session_id: str, head_msg_id: str) -> int:
        from .deletion import delete_nodes
        return delete_nodes(self, session_id, head_msg_id, descendants=True)
