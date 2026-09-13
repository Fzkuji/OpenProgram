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
        from .append import append_node
        append_node(self, session_id, shared._msg_to_node(msg), tip_only=True)


    def append_messages(self, session_id: str, msgs: list[dict[str, shared.Any]]) -> None:
        for m in msgs:
            self.append_message(session_id, m)


    @staticmethod
    def _history_node_path(git, node):
        return git.path / "history" / (
            f"{node.seq:04d}-{(node.role or 'x')[0]}-{node.id}.json"
        )

    @shared.contextmanager
    def _session_write_scope(self, session_id, *, create_if_missing=False):
        """Keep placement, index refresh and writes in one writer scope."""
        with self._session_lock(session_id):
            pair = self._open(session_id, create_if_missing=create_if_missing)
            if pair is None:
                yield None
                return
            git, idx = pair
            old_path = git.path
            with self._head_file_lock(git), idx._persist_lock:
                if git.path != old_path or git.stale():
                    paths, meta = git.list_history(), git.read_meta()
                    pending_creation = (
                        create_if_missing and not paths and not meta
                        and idx.meta.get("id") == session_id
                    )
                    if not pending_creation:
                        idx.rebuild_from_paths(
                            paths, meta,
                            shared._node_conv_predecessor, shared._node_caller,
                        )
                    git.mark_synced()
                yield git, idx

    def _update_history_node(self, session_id, git, idx, node_id, fields):
        cached = idx.nodes_by_id.get(node_id)
        if cached is None:
            return
        path = self._history_node_path(git, cached)
        try:
            payload = shared.json.loads(shared.read_text_with_retry(path))
        except FileNotFoundError:
            return
        node = shared.Call(**{
            key: value for key, value in payload.items()
            if key in shared.Call.__dataclass_fields__
        })
        if (node.id, node.seq, node.role) != (cached.id, cached.seq, cached.role):
            raise ValueError(f"history node identity mismatch: {node_id}")
        # Apply changes to a detached durable snapshot, never to the cache
        # before the file replacement has succeeded.
        for key, value in fields.items():
            if key != "metadata":
                setattr(node, key, value)
        if "output" in fields:
            self.spill_large_node(session_id, node)
        metadata = fields.get("metadata")
        if isinstance(metadata, dict):
            current = node.metadata if isinstance(node.metadata, dict) else {}
            node.metadata = {**current, **metadata}
        for edge in ("predecessor", "caller"):
            value = getattr(node, edge)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{edge} must be a string or None")
        if self._history_node_path(git, node) != path:
            raise ValueError("updating a node cannot change its history filename")
        shared.atomic_write_text(
            path, shared.json.dumps(node.to_dict(), ensure_ascii=False, default=str),
        )
        old_predecessor = cached.predecessor or None
        old_caller = cached.caller or None
        cached.__dict__.update(node.__dict__)
        idx.reindex_edges(
            node_id, old_predecessor=old_predecessor, old_caller=old_caller,
        )

    def update_node(
        self, session_id: str, node_id: str, **fields: shared.Any,
    ) -> None:
        """Update one current node and rewrite its history file once."""
        with self._session_write_scope(session_id) as pair:
            if pair is not None:
                self._update_history_node(session_id, *pair, node_id, fields)

    def merge_node_metadata_batch(
        self,
        session_id: str,
        patches: dict[str, dict[str, shared.Any]],
    ) -> None:
        """Merge current durable metadata with one open and writer scope."""
        with self._session_write_scope(session_id) as pair:
            if pair is not None:
                for node_id, patch in patches.items():
                    self._update_history_node(
                        session_id, *pair, node_id, {"metadata": patch},
                    )


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

