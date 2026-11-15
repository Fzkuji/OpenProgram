"""SessionStore branches operations."""
from __future__ import annotations
from . import shared


def _subtree(idx, head_msg_id: str) -> list[str]:
    """A node and everything under it, by both conversation and call edges."""
    found: list[str] = [head_msg_id]
    seen: set[str] = {head_msg_id}
    stack: list[str] = [head_msg_id]
    while stack:
        cur = stack.pop()
        for cid in [*idx.children_by_predecessor.get(cur, []), *idx.children_by_caller.get(cur, [])]:
            if cid not in seen:
                seen.add(cid)
                found.append(cid)
                stack.append(cid)
    return found


def _record_tip(meta: dict, nodes: dict, head: str) -> str:
    """The tip whose record a write addressed to ``head`` belongs to.

    A writer names the tip it saw: the client renames the tip on its
    screen, a turn's background namer the reply it finished. The
    branch's record moves with every message the branch adds
    (``branch_config.follow_line`` / ``carry_branch``), so by the time
    the write lands ``head`` may be a tip the branch has moved past.
    Such a ``head`` — the message a recorded branch's latest turn
    extended, or one of that turn's own user messages — is that
    branch's, and the write goes to its record at its tip now. A
    ``head`` that has a record, ends a recorded branch, is not written
    yet, or leads to more than one branch is taken as it is.
    """
    named = meta.get("branches") or {}
    refs = meta.get("branch_refs") or {}
    live = [ref.get("head_id") for ref in refs.values()
            if isinstance(ref, dict) and ref.get("status") != "aborted"]
    if head in named or head in live or head not in nodes:
        return head
    found = set()
    for tip in live:
        node = nodes.get(tip)
        seen = {tip}
        while node is not None:
            prev = shared._node_conv_predecessor(node)
            if not prev or prev in seen:
                break
            if prev == head:
                found.add(tip)
                break
            seen.add(prev)
            node = nodes.get(prev)
            if node is None or node.role != shared.ROLE_USER:
                break
    return found.pop() if len(found) == 1 else head


class BranchesOperations:
    def get_branch(
        self,
        session_id: str,
        head_msg_id: shared.Optional[str] = None,
    ) -> list[dict[str, shared.Any]]:
        pair = self._open(session_id)
        if pair is None:
            return []
        _session_log, idx = pair
        head = head_msg_id or idx.head_id
        hidden = set(idx.meta.get("rewind_hidden_nodes") or [])
        retired = set(idx.meta.get("rewind_hidden_heads") or [])
        if not head or head not in idx.nodes_by_id or head in hidden or head in retired:
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
            and n.id not in hidden
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
        session_log, idx = pair
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
        from ..branch_config import advance_branch
        with self._head_file_lock(session_log):
            meta = session_log.read_meta()
            advance_branch(meta, session_id, head_id, idx.nodes_by_id)
            with idx._persist_lock:
                with idx._lock:
                    idx.meta = meta
                    idx.head_id = head_id
                session_log.write_meta(meta)



    def compare_and_set_head(
        self,
        session_id: str,
        expected_head_id: shared.Optional[str],
        new_head_id: shared.Optional[str],
        *,
        expected_meta: shared.Optional[dict[str, shared.Any]] = None,
        branch_update: shared.Optional[dict[str, shared.Any]] = None,
        meta_update: shared.Optional[dict[str, shared.Any]] = None,
    ) -> bool:
        """Durable cross-process HEAD CAS, optionally activating a branch ref.

        Branch records are changed from the record read inside the lock:
        ``meta_update[BRANCH_CHECKOUT]`` switches branches
        (``rewind_view.apply_checkout``) and
        ``meta_update[BRANCH_RECORD_MOVES]`` (``[[from_tip, to_tip], ...]``)
        moves name records to a branch's new tip. Whole ``branches`` /
        ``branch_refs`` / ``active_branch_id`` values in ``meta_update``
        are ignored.
        """
        from ..branch_config import move_branch_record, park_record
        from ..rewind_view import BRANCH_CHECKOUT, BRANCH_RECORD_MOVES, apply_checkout
        with self._session_lock(session_id):
            pair = self._open(session_id)
            if pair is None:
                return False
            session_log, idx = pair
            node = idx.nodes_by_id.get(new_head_id) if new_head_id else None
            if node is not None and (node.metadata or {}).get("covers_ids"):
                raise ValueError(
                    f"compare_and_set_head: {new_head_id!r} is a compaction summary"
                )
            with self._head_file_lock(session_log):
                try:
                    durable = session_log.read_meta()
                    if durable.get("head_id") != expected_head_id:
                        return False
                    if expected_meta and any(durable.get(key) != value for key, value in expected_meta.items()):
                        return False
                    updated = dict(durable)
                    meta_update = dict(meta_update or {})
                    checkout = meta_update.pop(BRANCH_CHECKOUT, None)
                    moves = meta_update.pop(BRANCH_RECORD_MOVES, None) or []
                    # Branch records change only through the merges below,
                    # on the records read here inside the lock. A whole map
                    # in the update is a copy read earlier (an intent
                    # journaled before this rule) and would put back what
                    # was written since; it is not applied.
                    for key in ("branches", "branch_refs", "active_branch_id"):
                        meta_update.pop(key, None)
                    if checkout is not None:
                        apply_checkout(updated, session_id, checkout.get("target"),
                                       checkout.get("branch_id"), idx.nodes_by_id)
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
                            park_record(updated, refs, source_id)
                        if target_id and not branch_update.get("preserve_target"):
                            target = dict(refs.get(target_id) or {})
                            target.update({
                                "branch_id": target_id,
                                "head_id": new_head_id,
                                **({"parent_branch_id": source_id} if source_id != target_id else {}),
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
                    owner = updated.get("active_branch_id")
                    for move in moves:
                        if isinstance(move, (list, tuple)) and len(move) == 2:
                            move_branch_record(updated, move[0], move[1], owner=owner)
                    if meta_update:
                        updated.update(meta_update)
                    session_log.write_meta(updated)
                    with idx._persist_lock:
                        with idx._lock:
                            idx.head_id = new_head_id
                            idx.meta = updated
                    if updated.get("agent_id") != durable.get("agent_id"):
                        self._update_index_entry(session_id, agent_id=updated.get("agent_id"))
                        self._schedule_index_flush()
                    return True
                finally:
                    pass


    def message_exists(self, session_id: str, msg_id: str) -> bool:
        pair = self._open(session_id)
        if pair is None:
            return False
        _session_log, idx = pair
        return msg_id in idx.nodes_by_id


    def has_persisted_ancestor(
        self, session_id: str, ancestor_id: str, descendant_id: str,
    ) -> bool:
        """Read the on-disk predecessor chain, without trusting cached nodes."""
        pair = self._open(session_id)
        if pair is None or not ancestor_id or not descendant_id:
            return False
        session_log, _idx = pair
        nodes = {payload.get("id"): payload for payload in session_log.list_history()}
        visited = set()
        current = descendant_id
        while current and current not in visited:
            visited.add(current)
            node = nodes.get(current)
            if node is None:
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
        _session_log, idx = pair
        # A branch tip is a conv node (no caller) with no conv-child.
        tips: list[dict[str, shared.Any]] = []
        named = (idx.meta.get("branches") or {})
        hidden = set(idx.meta.get("rewind_hidden_nodes") or [])
        hidden_heads = set(idx.meta.get("rewind_hidden_heads") or [])

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
            if child.id in hidden:
                return False
            md = child.metadata or {}
            if md.get("structural"):
                return False
            # A runtime-scheduled turn (an agent's completion notice, a
            # merge prompt) is a user row the conversation continues
            # through; other runtime rows are cards off to the side.
            if md.get("display") == "root" or (
                md.get("display") == "runtime" and child.role != shared.ROLE_USER
            ):
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

        from ..branch_config import branch_record
        retained_tips = {ref.get("head_id") for ref in (idx.meta.get("branch_refs") or {}).values()
                         if ref.get("status") != "aborted"}
        merged = self.merged_heads(session_id)
        # The conversation's head is a branch end even while a background
        # branch (a scheduled task's) leaves from it.
        head = idx.head_id
        carried_on: set[str] = set()

        for node in idx.all_nodes():
            if not _conversation_node(node) or node.id in hidden_heads:
                continue
            kids = idx.children_by_predecessor.get(node.id, [])
            def _opens_task_branch(kid_id: str) -> bool:
                """A scheduled task's first message leaves this line without ending it."""
                kid_md = idx.nodes_by_id[kid_id].metadata or {}
                return kid_md.get("source") == "scheduler" and (node.metadata or {}).get("source") != "scheduler"

            continued = [k for k in kids if _conv_child(k) and not _opens_task_branch(k)]
            side_only = node.id == head and all(
                idx.nodes_by_id[k].role == shared.ROLE_USER for k in continued)
            if continued and not side_only:
                carried_on.add(node.id)
                if node.id not in retained_tips:
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
        refs = idx.meta.get("branch_refs") or {}
        active_id = idx.meta.get("active_branch_id")
        referenced = {ref.get("head_id") for ref in refs.values() if ref.get("status") != "aborted"}
        tips = [tip for tip in tips if tip["head_msg_id"] not in referenced]
        # A conversation with one recorded branch has no sibling to tell apart:
        # a record the line has since carried on past is only behind, not a tip.
        only_line = len(referenced) == 1
        for branch_id, ref in refs.items():
            tip = ref.get("head_id")
            if ref.get("status") == "aborted" or tip in hidden or tip in hidden_heads or tip not in idx.nodes_by_id:
                continue
            if tip in merged or (only_line and tip in carried_on):
                continue
            # Selected or not, the name is the branch's record (its tip's).
            label = branch_record(idx.meta, branch_id)
            config = ref.get("config") if isinstance(ref.get("config"), dict) else {}
            agent_id = idx.meta.get("agent_id") if branch_id == active_id else config.get("agent_id")
            tips.append({"head_msg_id": tip, "branch_id": branch_id, "name": label.get("name"),
                         "created_at": label.get("created_at"), "updated_at": label.get("updated_at"),
                         "archived": bool(label.get("archived")),
                         **({"agent_id": agent_id} if agent_id else {})})
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
        wipe the lock, the counters, or the archive flag. An automatic
        name (``auto_named``) follows a turn that was written, so it
        never opens a record for a message that is not there (a branch
        deleted while its name was being made)."""
        pair = self._open(session_id)
        if pair is None:
            return
        _session_log, idx = pair
        auto = bool(fields.get("auto_named"))

        def _mutate(entry: dict):
            if auto and entry.get("name_locked"):
                # Read on the record inside the lock: a name the person
                # gave while the label was being made wins.
                return False
            now = shared.time.time()
            entry["name"] = name
            entry.setdefault("created_at", now)
            entry["updated_at"] = now
            entry.update(fields)

        self._merge_branch_entry(session_id, head_msg_id, _mutate,
                                 create=not auto or head_msg_id in idx.nodes_by_id)


    def name_spawned_branch(self, session_id: str, tip: str, label: str) -> bool:
        """Give a spawned task's branch its label as its name (the Spawn
        tier of branch-naming). ``tip`` may be the reply the run has not
        written yet; the record waits there for it. A branch that already
        has a name (renamed, or named automatically) keeps it."""
        def _mutate(entry: dict):
            if entry.get("name"):
                return False
            now = shared.time.time()
            entry["name"] = label
            entry.setdefault("created_at", now)
            entry["updated_at"] = now

        return self._merge_branch_entry(session_id, tip, _mutate) is not None


    def _merge_branch_entry(self, session_id: str, head_msg_id: str, mutate, *,
                            create: bool = True) -> shared.Optional[dict]:
        """Merge one branch record on disk under the session write lock.

        ``mutate(entry)`` sets only the fields its writer owns, on the
        record as it is on disk inside the lock — never on a copy read
        earlier — so two writers never drop each other's fields. An
        archived record stays archived whatever a later write says, and
        with ``create`` false a missing record stays missing. Returns the
        merged record, or None when nothing was written.
        """
        pair = self._open(session_id)
        if pair is None:
            return None
        session_log, idx = pair

        def _apply(meta: dict):
            branches = dict(meta.get("branches") or {})
            tip = _record_tip(meta, idx.nodes_by_id, head_msg_id)
            current = branches.get(tip)
            if not isinstance(current, dict) and not create:
                return False, None
            entry = dict(current) if isinstance(current, dict) else {}
            archived = {k: entry[k] for k in ("archived", "archived_at", "archived_reason")
                        if k in entry} if entry.get("archived") else {}
            if mutate(entry) is False:
                return False, None
            entry.update(archived)
            branches[tip] = entry
            meta["branches"] = branches
            return True, dict(entry)

        return self._update_meta(session_log, idx, _apply)


    def reserve_task_branch(
        self,
        session_id: str,
        *,
        task_id: str,
        msg_id: str,
        agent_id: shared.Optional[str],
        label: shared.Any = None,
    ) -> shared.Optional[str]:
        """Give a scheduled task's run its branch record (``reserve_task_branch``)."""
        pair = self._open(session_id)
        if pair is None:
            return None
        session_log, idx = pair
        from ..branch_config import reserve_task_branch
        with self._head_file_lock(session_log):
            meta = session_log.read_meta()
            branch_id = reserve_task_branch(meta, session_id, task_id=task_id, msg_id=msg_id,
                                            agent_id=agent_id, label=label)
            with idx._persist_lock:
                with idx._lock:
                    idx.meta = meta
                    idx.head_id = meta.get("head_id")
                session_log.write_meta(meta)
        return branch_id


    def point_branch(self, session_id: str, branch_id: str, *,
                     expected_head: shared.Optional[str], head: str) -> bool:
        """Move an unselected branch's record onto a message just written
        under its tip (``branch_config.point_branch``); the head stays."""
        pair = self._open(session_id)
        if pair is None:
            return False
        session_log, idx = pair
        from ..branch_config import point_branch
        with self._head_file_lock(session_log):
            meta = session_log.read_meta()
            if not point_branch(meta, branch_id, expected_head=expected_head, head=head):
                return False
            with idx._persist_lock:
                with idx._lock:
                    idx.meta = meta
                    idx.head_id = meta.get("head_id")
                session_log.write_meta(meta)
        return True


    def update_branch_fields(self, session_id: str, branch_id: str, **fields: shared.Any) -> bool:
        """Record facts about a branch's runs on its record
        (``branch_config.set_branch_fields``), selected or not."""
        pair = self._open(session_id)
        if pair is None:
            return False
        session_log, idx = pair
        from ..branch_config import set_branch_fields
        with self._head_file_lock(session_log):
            meta = session_log.read_meta()
            if not set_branch_fields(meta, branch_id, fields):
                return False
            with idx._persist_lock:
                with idx._lock:
                    idx.meta = meta
                    idx.head_id = meta.get("head_id")
                session_log.write_meta(meta)
        return True


    def branch_entries(self, session_id: str) -> dict[str, dict[str, shared.Any]]:
        """Every branch meta entry of the session, keyed by tip."""
        pair = self._open(session_id)
        if pair is None:
            return {}
        _session_log, idx = pair
        return {head: dict(entry) for head, entry in (idx.meta.get("branches") or {}).items()
                if isinstance(entry, dict)}


    def get_branch_meta(self, session_id: str, head_msg_id: str) -> dict[str, shared.Any]:
        """Return a branch's full meta entry (name + auto-naming state),
        or ``{}`` if the branch has no entry. Used by the auto-namer to
        re-read the lock before writing back (see branch-naming.md
        "优先级与锁")."""
        pair = self._open(session_id)
        if pair is None:
            return {}
        _session_log, idx = pair
        return dict((idx.meta.get("branches") or {}).get(head_msg_id) or {})


    def set_branch_meta(
        self, session_id: str, head_msg_id: str, **fields: shared.Any,
    ) -> None:
        """Merge ``fields`` into a branch's meta entry without touching
        its name. Used for lifecycle facts that ride the same entry as
        the name (``archived`` / ``archived_at`` / ``archived_reason``
        — see agent-collaboration.md, archiving). The tip may be a reply
        admitted but not yet written (``close_agent`` on a running
        agent), so the record is opened even before its message exists.
        Archiving cannot be undone: a branch archived first keeps its
        own ``archived_at``."""
        def _mutate(entry: dict) -> None:
            now = shared.time.time()
            entry.update(fields)
            entry.setdefault("created_at", now)
            entry["updated_at"] = now

        self._merge_branch_entry(session_id, head_msg_id, _mutate)


    def bump_branch_turns(self, session_id: str, head_msg_id: str) -> int:
        """Increment a branch's per-branch turn counter and return the
        new value. Used by finalize_turn to decide whether to trigger
        Stage-2 auto-rename (counter, not a message count — see
        branch-naming.md 第四节). Returns 0, writing nothing, for a tip
        that is not a message of the conversation (deleted meanwhile)."""
        pair = self._open(session_id)
        if pair is None:
            return 0
        _session_log, idx = pair

        def _mutate(entry: dict) -> None:
            entry["turns"] = int(entry.get("turns", 0)) + 1

        merged = self._merge_branch_entry(session_id, head_msg_id, _mutate,
                                          create=head_msg_id in idx.nodes_by_id)
        return int((merged or {}).get("turns", 0))


    def carry_branch_entry(self, session_id: str, head_msg_id: str) -> bool:
        """Move the entry of the tip this turn extended onto ``head_msg_id``.

        Entries are keyed by tip, and every turn moves the tip. Without
        the carry each turn opened a fresh entry: a name the user gave
        (and its lock) stayed behind on the old tip, and the branch's
        own turn counter never got past 1, so Stage 2 renamed the
        branch after every turn (branch-naming.md §4).

        The extended tip is found by walking back from the new head
        through this turn's user messages (a steer adds a second one)
        to the first node that is not a user message, or to the spawn
        root that opened the branch. A fork point already had another
        conversation child before this turn, so its entry (if any)
        belongs to the line that left it first and is left alone; the
        fork starts with a fresh entry like any new branch. The records
        are read and moved on disk inside the write lock.
        """
        pair = self._open(session_id)
        if pair is None:
            return False
        session_log, idx = pair
        node = idx.nodes_by_id.get(head_msg_id)
        if node is None:
            return False

        def _apply(meta: dict):
            named = dict(meta.get("branches") or {})
            if head_msg_id in named:
                return False, False
            source = None
            seen: set[str] = set()
            cur = shared._node_conv_predecessor(node)
            while cur and cur not in seen:
                seen.add(cur)
                if cur in named:
                    source = cur
                    break
                prev = idx.nodes_by_id.get(cur)
                if prev is None or prev.role != shared.ROLE_USER:
                    break
                cur = shared._node_conv_predecessor(prev)
            if source is None:
                return False, False
            hidden = set(meta.get("rewind_hidden_nodes") or [])
            conv_kids = [
                k for k in idx.children_by_predecessor.get(source, [])
                if k not in hidden and (kid := idx.nodes_by_id.get(k)) is not None
                and kid.role in (shared.ROLE_USER, shared.ROLE_LLM)
                and (kid.metadata or {}).get("display") != "root"
                and not ((kid.metadata or {}).get("display") == "runtime"
                         and kid.role != shared.ROLE_USER)
                and (kid.metadata or {}).get("function") != "attach"
            ]
            resumes_rewind = any(
                edge.get("target_head_id") == source and edge.get("status") == "rewound"
                and edge.get("target_branch_id") == meta.get("active_branch_id")
                for edge in meta.get("rewind_edges") or []
            )
            if len(conv_kids) > 1 and not resumes_rewind:
                return False, False
            named[head_msg_id] = named.pop(source)
            meta["branches"] = named
            return True, True

        return self._update_meta(session_log, idx, _apply)


    def delete_branch_name(self, session_id: str, head_msg_id: str) -> None:
        pair = self._open(session_id)
        if pair is None:
            return
        session_log, idx = pair

        def _apply(meta: dict):
            branches = dict(meta.get("branches") or {})
            if branches.pop(head_msg_id, None) is None:
                return False, None
            meta["branches"] = branches
            return True, None

        self._update_meta(session_log, idx, _apply)


    def branch_tail(self, session_id: str, head_msg_id: str) -> dict[str, shared.Any]:
        """The part of a branch that only this branch holds.

        ``head_msg_id`` names any message on the branch. Its tip is found
        by following the one conversation child down; its tail starts
        where, walking up, a message's predecessor has another
        conversation child (the fork point). Deleting a branch deletes
        this tail, so the fork point and everything above it, which other
        branches share, stay. Returns ``{"start", "tip", "fork", "nodes"}``;
        raises ``ValueError`` when the message is unknown, when several
        branches continue below it, or when the branch is the
        conversation's only line (nothing forks from it). ``nodes`` lists
        every message the delete removes.
        """
        pair = self._open(session_id)
        if pair is None:
            raise ValueError("Unknown conversation.")
        _session_log, idx = pair
        hidden = set(idx.meta.get("rewind_hidden_nodes") or [])
        node = idx.nodes_by_id.get(head_msg_id)
        if node is None or head_msg_id in hidden:
            raise ValueError("That message is not on any branch.")

        def _conv_kids(parent: str) -> list[str]:
            kids = []
            for kid_id in idx.children_by_predecessor.get(parent, []):
                kid = idx.nodes_by_id.get(kid_id)
                if kid is None or kid_id in hidden or kid.role not in (shared.ROLE_USER, shared.ROLE_LLM):
                    continue
                md = kid.metadata or {}
                if (md.get("display") == "root" or md.get("structural")
                        or md.get("function") == "attach"
                        or (md.get("display") == "runtime" and kid.role != shared.ROLE_USER)):
                    continue
                kids.append(kid_id)
            return kids

        tip = head_msg_id
        seen = {tip}
        while True:
            kids = _conv_kids(tip)
            if not kids:
                break
            if len(kids) > 1:
                raise ValueError("Several branches continue from that message; pick one of their tips.")
            tip = kids[0]
            if tip in seen:
                break
            seen.add(tip)
        start = head_msg_id
        seen = {start}
        while True:
            pred = shared._node_conv_predecessor(idx.nodes_by_id[start])
            pred_node = idx.nodes_by_id.get(pred) if pred else None
            if pred_node is None:
                raise ValueError("This is the conversation's only branch; delete the conversation instead.")
            if len(_conv_kids(pred)) > 1:
                return {"start": start, "tip": tip, "fork": pred,
                        "nodes": _subtree(idx, start)}
            if ((pred_node.metadata or {}).get("display") == "root"
                    or pred_node.role not in (shared.ROLE_USER, shared.ROLE_LLM)
                    or pred in seen):
                raise ValueError("This is the conversation's only branch; delete the conversation instead.")
            seen.add(pred)
            start = pred


    def delete_branch_tail(self, session_id: str, head_msg_id: str) -> int:
        pair = self._open(session_id)
        if pair is None:
            return 0
        session_log, idx = pair
        if head_msg_id not in idx.nodes_by_id:
            return 0
        # Fork point the doomed subtree hangs off — the head fallback
        # if the session head turns out to live inside the subtree.
        root_pred = shared._node_conv_predecessor(idx.nodes_by_id[head_msg_id])
        to_delete = _subtree(idx, head_msg_id)
        seen: set[str] = set(to_delete)

        session_log.delete_nodes(to_delete)
        # Drop from index. ID set kept so the
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
        # Drop named branches for the deleted heads. A branch record whose
        # tip was deleted is gone with it: kept, it still listed the
        # branch, pointed at a missing message, and took a message sent
        # to it. The selected record stays for the caller to move off (a
        # checkout before the delete). A head inside the deleted subtree
        # must not survive the deletion — a dangling head renders the
        # session empty. Fall back to the fork point the subtree hung off
        # (None = the tail was the whole session). Callers that pick a
        # smarter new head (the ws handler moves to another named leaf)
        # overwrite this. All read and written on disk under the lock.
        def _apply(meta: dict):
            branches = dict(meta.get("branches") or {})
            for nid in to_delete:
                branches.pop(nid, None)
            meta["branches"] = branches
            refs = dict(meta.get("branch_refs") or {})
            active_id = meta.get("active_branch_id")
            dropped = [key for key, ref in refs.items()
                       if key != active_id and isinstance(ref, dict) and ref.get("head_id") in seen]
            for key in dropped:
                refs.pop(key)
            if dropped:
                meta["branch_refs"] = refs
            if meta.get("head_id") in seen or idx.head_id in seen:
                meta["head_id"] = root_pred
            return True, None

        self._update_meta(session_log, idx, _apply,
                          keys=("branches", "branch_refs", "head_id"))
        return len(to_delete)
