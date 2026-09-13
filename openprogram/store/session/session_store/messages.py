"""SessionStore messages operations."""
from __future__ import annotations
from . import shared


class MessagesOperations:
    def spill_large_node(self, session_id: str, node) -> None:
        """Write an over-cap node's full text to ``large_nodes/`` and stamp
        ``metadata.spilled`` on it, before the node is written to history.

        Recording is the one moment a node's text is genuinely new, so it
        is the one place spilling belongs — rendering stays a pure read.
        Must run AFTER seq assignment (the seq names the file) and BEFORE
        ``write_history`` (so the stamp lands in the persisted node).
        """
        try:
            from openprogram.context import spill as _spill
            from openprogram.context.spill import spill_if_large, large_dir_for
            text = node.output
            # Cheap guard first: the overwhelming majority of nodes are
            # small, and this runs on every single append. Without it
            # every append would pay a filesystem round-trip.
            if (not isinstance(text, str)
                    or len(text) <= _spill.NODE_RENDER_CAP
                    or not _spill.SPILL_ENABLED):
                return
            stamp = spill_if_large(
                text,
                node_key=f"{node.seq:04d}-{node.id}",
                large_dir=large_dir_for(
                    str(self._session_dir(session_id) / "history")),
            )
            if stamp:
                meta = dict(node.metadata or {})
                meta["spilled"] = stamp
                node.metadata = meta
        except Exception as e:  # noqa: BLE001
            # Spilling is an optimisation; never block the write.
            shared._log.debug("spill skipped for %s: %s", session_id, e)


    def append_message(self, session_id: str, msg: dict[str, shared.Any]) -> None:
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        git, idx = pair
        node = shared._msg_to_node(msg)
        # The complete read/modify/write sequence is protected by the
        # session-ID lock. Placement may change while a process is alive, so
        # acquire through _head_file_lock and use its revalidated path.
        old_path = git.path
        with self._head_file_lock(git):
            if git.path != old_path:
                idx.reset()
                idx.rebuild_from_paths(git.list_history(), git.read_meta(),
                                       shared._node_conv_predecessor, shared._node_caller)
            # Idempotent — skip if id already known.
            if node.id in idx.nodes_by_id:
                return
            predecessor = shared._node_conv_predecessor(node)
            caller = shared._node_caller(node)
            shared._check_append_invariant(session_id, idx, node, predecessor, caller)
            seq = idx.append(node, predecessor=predecessor, caller=caller)
            self.spill_large_node(session_id, node)
            # Write the raw node file. Commit deferred to turn end.
            git.write_history(seq, node.role, node.id, node.to_dict())
            # Advance head only when the conversation actually grew: a
        # caller-less node chained onto the current tip (or the session's
        # first node). Any other insert — a compaction summary splicing
        # mid-chain, a side-branch write — leaves head alone; explicit
        # moves go through set_head (context/compaction.md §5).
            advanced = (not caller
                        and (idx.head_id is None or predecessor == idx.head_id))
            if advanced:
                idx.set_head(node.id)
            activity_at = shared.time.time()
            idx.set_meta(updated_at=activity_at)
            # Persist activity time while the same session lock is held.
            with idx._persist_lock:
                with idx._lock:
                    meta = dict(idx.meta)
                    meta["head_id"] = idx.head_id
                git.write_meta(meta)
        # Registry: every appended message bumps updated_at（最新一次聊天
        # 时间，侧栏排序键）；user 消息顺带刷新 preview（debounced to disk）。
        fields: dict[str, shared.Any] = {"updated_at": activity_at}
        if node.role == "user" and node.output:
            text = (node.output or "").strip().replace("\n", " ")
            fields["preview"] = (text[:77] + "…") if len(text) > 80 else text
        self._update_index_entry(session_id, **fields)
        self._schedule_index_flush()


    def append_messages(self, session_id: str, msgs: list[dict[str, shared.Any]]) -> None:
        for m in msgs:
            self.append_message(session_id, m)


    @staticmethod
    def _rewrite_history_node(git: shared.GitSession, node: shared.Call) -> None:
        """Atomically replace one existing history node without syncing it."""
        role_letter = (node.role or "x")[0]
        path = git.path / "history" / (
            f"{node.seq:04d}-{role_letter}-{node.id}.json"
        )
        if path.exists():
            shared.atomic_write_text(
                path,
                shared.json.dumps(node.to_dict(), ensure_ascii=False, default=str),
            )


    def update_node(
        self, session_id: str, node_id: str, **fields: shared.Any,
    ) -> None:
        """Update one current node and rewrite its history file once."""
        pair = self._open(session_id)
        if pair is None:
            return
        git, idx = pair
        node = idx.nodes_by_id.get(node_id)
        if node is None:
            return
        old_predecessor = node.predecessor or None
        old_caller = node.caller or None
        metadata = fields.pop("metadata", {})
        for key, value in fields.items():
            setattr(node, key, value)
        if "output" in fields:
            self.spill_large_node(session_id, node)
        if isinstance(metadata, dict):
            current = node.metadata if isinstance(node.metadata, dict) else {}
            node.metadata = {**current, **metadata}
        idx.reindex_edges(
            node_id,
            old_predecessor=old_predecessor,
            old_caller=old_caller,
        )
        old_path = git.path
        with self._head_file_lock(git):
            if git.path != old_path:
                idx.reset()
                idx.rebuild_from_paths(git.list_history(), git.read_meta(),
                                       shared._node_conv_predecessor, shared._node_caller)
            self._rewrite_history_node(git, node)


    def merge_node_metadata_batch(
        self,
        session_id: str,
        patches: dict[str, dict[str, shared.Any]],
    ) -> None:
        """Merge one session's node metadata with one index open."""
        pair = self._open(session_id)
        if pair is None:
            return
        git, idx = pair
        for node_id, patch in patches.items():
            node = idx.nodes_by_id.get(node_id)
            if node is None:
                continue
            current = node.metadata if isinstance(node.metadata, dict) else {}
            node.metadata = {**current, **patch}
            with self._head_file_lock(git):
                self._rewrite_history_node(git, node)


    def merge_node_metadata(
        self, session_id: str, node_id: str, patch: dict[str, shared.Any],
    ) -> None:
        """Merge metadata into one persisted node without touching session meta."""
        self.merge_node_metadata_batch(session_id, {node_id: patch})


    def get_messages(self, session_id: str, *, limit: shared.Optional[int] = None) -> list[dict[str, shared.Any]]:
        pair = self._open(session_id)
        if pair is None:
            return []
        _git, idx = pair
        msgs = [
            shared._node_to_msg(n, session_id) for n in idx.all_nodes()
            if (n.metadata or {}).get("display") != "root"
            and not (n.metadata or {}).get("rewound")
            # ``context/*`` nodes record what the context pipeline sent
            # (dag/overview.md §7). They are machinery, not conversation, and
            # stay out of every chat/transcript view. ``get_nodes`` is the
            # raw view for the code that does want them.
            # ``context/summary`` is the exception: §8 makes it an ordinary
            # chain member carrying real conversation content (the recap
            # that stands in for the range it covers), so it is painted
            # and read like any other turn.
            and not shared._is_hidden_context_node(n)
        ]
        if limit is not None:
            msgs = msgs[-limit:]
        return msgs

