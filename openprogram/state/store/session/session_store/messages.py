"""SessionStore messages operations."""
from __future__ import annotations
from . import shared


def _is_conversation_node(node, hidden=()) -> bool:
    """Whether a node is part of the conversation a chat or transcript reads."""
    metadata = node.metadata or {}
    # ``context/*`` nodes record what the context pipeline sent
    # (dag/overview.md §7). They are machinery, not conversation, and
    # stay out of every chat/transcript view. ``get_nodes`` is the
    # raw view for the code that does want them.
    # ``context/summary`` is the exception: §8 makes it an ordinary
    # chain member carrying real conversation content (the recap
    # that stands in for the range it covers), so it is painted
    # and read like any other turn.
    return (
        node.id not in hidden
        and metadata.get("display") != "root"
        and not metadata.get("rewound")
        and not metadata.get("structural")
        and not shared._is_hidden_context_node(node)
    )


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
            from .. import spill as _spill
            from ..spill import spill_if_large, large_dir_for
            text = node.output
            # Cheap guard first: the overwhelming majority of nodes are
            # small, and this runs on every single append. Without it
            # every append would pay a filesystem round-trip.
            if (not isinstance(text, str)
                    or len(text) <= _spill.NODE_RENDER_CAP
                    or not _spill.SPILL_ENABLED
                    or _spill.bounded_when_made(node)
                    or _spill.never_cut(node)):
                return
            stamp = spill_if_large(
                text,
                node_key=f"{node.seq:04d}-{node.id}",
                large_dir=large_dir_for(str(self._session_dir(session_id))),
            )
            if stamp:
                meta = dict(node.metadata or {})
                meta["spilled"] = stamp
                node.metadata = meta
        except Exception as e:  # noqa: BLE001
            # Spilling is an optimisation; never block the write.
            shared._log.debug("spill skipped for %s: %s", session_id, e)


    def append_turn_record(self, session_id: str, record_type: str, data: dict[str, shared.Any]) -> None:
        """Add one line of a running turn's progress to the session's log."""
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            raise KeyError(f"session {session_id} has no log")
        pair[0].append_turn_record(record_type, data)

    def append_run_rows(self, session_id: str, rows: list[dict]) -> None:
        """Log one committed change of run state.

        Called with the run tables locked, so it takes no session lock: an
        open session keeps its own log object, so its index stays in step.
        """
        if shared.is_deleted(self.root_path, session_id):
            return
        cached = self._sessions.get(session_id)
        session_log = cached[0] if cached else shared.SessionLog(self._session_dir(session_id))
        session_log.append_turn_record(shared.RUN_ROWS_RECORD, {"rows": rows})

    def state_file(self, session_id: str, name: str) -> "SessionStateFile":
        """A small named document of the session, kept in its log."""
        return shared.SessionStateFile(self, self._session_dir(session_id), name)

    def state_file_at(self, directory: shared.Path, name: str) -> "SessionStateFile":
        return shared.SessionStateFile(self, directory, name)

    def write_state_line(self, directory: shared.Path, name: str, content: str | None) -> None:
        cached = self._sessions.get(directory.name)
        session_log = cached[0] if cached and cached[0].path == directory else shared.SessionLog(directory)
        session_log.append_state_file(name, content)

    def read_run_rows(self, session_id: str) -> list[list[dict]]:
        """Every logged change of a session's run state, oldest first."""
        return shared.SessionLog(self._session_dir(session_id)).read_run_rows()

    def read_turn_records(self, session_id: str, execution_id: str | None,
                          record_type: str | None = None) -> list[dict[str, shared.Any]]:
        """Every progress line one execution (``None``: every execution)
        wrote to the session's log; ``record_type`` keeps one kind."""
        pair = self._open(session_id)
        return [] if pair is None else pair[0].read_turn_records(execution_id, record_type)

    def append_message(
        self, session_id: str, msg: dict[str, shared.Any], *, advance_head: bool = True,
    ) -> None:
        """Record one message. ``advance_head=False`` is for rows that hang
        off the conversation, such as attach cards and notices."""
        self._append(session_id, msg, after_head=False, advance_head=advance_head)

    def append_after_head(self, session_id: str, msg: dict[str, shared.Any]) -> None:
        """Append ``msg`` chained onto the head as it stands under the write
        lock, so a writer that reads the head first cannot fork the chain.
        With no head yet it starts the main line at ROOT."""
        self._append(session_id, msg, after_head=True, advance_head=True)

    def _append(
        self, session_id: str, msg: dict[str, shared.Any], *, after_head: bool, advance_head: bool,
    ) -> None:
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        session_log, idx = pair
        node = shared._msg_to_node(msg)
        # The complete read/modify/write sequence is protected by the
        # session-ID lock. Placement may change while a process is alive, so
        # acquire through _head_file_lock and use its revalidated path.
        old_path = session_log.path
        with self._head_file_lock(session_log):
            if session_log.path != old_path:
                idx.rebuild(session_log.list_history(), session_log.read_meta(),
                                       shared._node_conv_predecessor, shared._node_caller)
            if after_head:
                # Another process may have moved the head while this one waited.
                self._absorb_foreign_writes(session_log, idx)
            # Idempotent — skip if id already known.
            if node.id in idx.nodes_by_id:
                return
            if after_head:
                # No head yet (only a background run's branch, which never
                # moves it): the message starts the main line at ROOT, as a
                # first chat turn does.
                node.predecessor = idx.head_id or "ROOT"
            predecessor = shared._node_conv_predecessor(node)
            caller = shared._node_caller(node)
            shared._check_append_invariant(session_id, idx, node, predecessor, caller)
            seq = idx.append(node, predecessor=predecessor, caller=caller)
            self.spill_large_node(session_id, node)
            # Record the raw node.
            session_log.write_history(seq, node.role, node.id, node.to_dict())
            # Advance head only when the conversation actually grew: a
        # caller-less node chained onto the current tip (or the session's
        # first node). Any other insert — a compaction summary splicing
        # mid-chain, a side-branch write — leaves head alone; explicit
        # moves go through set_head (context/compaction.md §5).
            advanced = (advance_head and not caller
                        and (idx.head_id is None or predecessor == idx.head_id))
            durable = None
            if advanced or not caller:
                # The branch records move on the record as it is on disk,
                # read inside this lock; the cache takes the result.
                from ..branch_config import BRANCH_CONFIG_FIELDS, advance_branch, carry_branch
                durable = session_log.read_meta()
                if advanced:
                    advance_branch(durable, session_id, node.id, idx.nodes_by_id)
                else:
                    carry_branch(durable, predecessor, node.id)
                with idx._lock:
                    for key in (*shared.BRANCH_RECORD_KEYS,
                                *(BRANCH_CONFIG_FIELDS if advanced else ())):
                        if key in durable:
                            idx.meta[key] = durable[key]
                        else:
                            idx.meta.pop(key, None)
                if advanced:
                    idx.set_head(node.id)
            activity_at = shared.time.time()
            idx.set_meta(updated_at=activity_at)
            # Persist activity time while the same session lock is held.
            with idx._persist_lock:
                session_log.write_meta(self._meta_for_write(session_log, idx, durable))
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


    def update_stream_progress(
        self, session_id: str, node_id: str, *, change: shared.Optional[dict] = None,
        quiet: bool = True, **fields: shared.Any,
    ) -> None:
        """Record a streaming reply's progress.

        ``fields`` are the reply as it now stands, for this process's index.
        ``change`` is what was added since the last write
        (``session_log.apply_stream_changes``) and is all the log records;
        without it the fields are recorded whole. ``quiet`` writes without
        fsync and tells no observer: a reply's streaming text.
        """
        self._update_node(session_id, node_id, fields, quiet=quiet, stream=change)

    def update_node(
        self, session_id: str, node_id: str, **fields: shared.Any,
    ) -> None:
        """Update one current node and record the changed fields."""
        self._update_node(session_id, node_id, fields, quiet=False)

    def merge_node(
        self,
        session_id: str,
        node_id: str,
        change: shared.Callable[[shared.Any], shared.Optional[dict[str, shared.Any]]],
    ) -> bool:
        """Update one node from the node as it is inside the conversation's
        write lock, never from a copy read earlier.

        Two writers that each own part of a node (a synchronous sub-agent's
        caller and the job runner both settle its attach card) keep each
        other's fields this way. ``change(node)`` returns the fields to
        write, as :meth:`update_node` takes them, or None to leave the node
        alone. Returns whether the node was written.
        """
        pair = self._open(session_id)
        if pair is None:
            return False
        with self._head_file_lock(pair[0]):
            # Reopened inside the lock: another process's write is read in.
            pair = self._open(session_id)
            node = pair[1].nodes_by_id.get(node_id) if pair is not None else None
            if node is None:
                return False
            fields = change(node)
            if fields is None:
                return False
            self._update_node(session_id, node_id, dict(fields), quiet=False)
            return True

    def _update_node(
        self, session_id: str, node_id: str, fields: dict[str, shared.Any], *, quiet: bool,
        stream: shared.Optional[dict] = None,
    ) -> None:
        pair = self._open(session_id)
        if pair is None:
            return
        session_log, idx = pair
        node = idx.nodes_by_id.get(node_id)
        if node is None:
            return
        old_predecessor = node.predecessor or None
        old_caller = node.caller or None
        metadata = fields.pop("metadata", {})
        extras = {key: fields.pop(key) for key in list(fields) if not hasattr(node, key)}
        if extras:
            # A field a node has no column for lives in its metadata, as it
            # does when the message is first appended.
            metadata = {**(metadata if isinstance(metadata, dict) else {}), **extras}
        for key, value in fields.items():
            setattr(node, key, value)
        if "output" in fields:
            self.spill_large_node(session_id, node)
        if isinstance(metadata, dict):
            current = node.metadata if isinstance(node.metadata, dict) else {}
            node.metadata = {**current, **metadata}
            if metadata:
                idx.metadata_patched(node)
        idx.reindex_edges(
            node_id,
            old_predecessor=old_predecessor,
            old_caller=old_caller,
        )
        old_path = session_log.path
        with self._head_file_lock(session_log):
            if session_log.path != old_path:
                idx.rebuild(session_log.list_history(), session_log.read_meta(),
                                       shared._node_conv_predecessor, shared._node_caller)
            current = node.to_dict()
            changed = {key: current[key] for key in fields}
            stamped = dict(metadata) if isinstance(metadata, dict) else {}
            if "output" in fields and isinstance(node.metadata, dict) and "spilled" in node.metadata:
                stamped["spilled"] = node.metadata["spilled"]
            if stream is not None:
                spilled = {"metadata": {"spilled": stamped["spilled"]}} if "spilled" in stamped else {}
                session_log.stream_node(node_id, {**stream, **spilled}, quiet=quiet)
            else:
                session_log.patch_node(node_id, changed, stamped, quiet=quiet)


    def merge_node_metadata_batch(
        self,
        session_id: str,
        patches: dict[str, dict[str, shared.Any]],
    ) -> None:
        """Merge one session's node metadata with one index open."""
        pair = self._open(session_id)
        if pair is None:
            return
        session_log, idx = pair
        for node_id, patch in patches.items():
            node = idx.nodes_by_id.get(node_id)
            if node is None:
                continue
            current = node.metadata if isinstance(node.metadata, dict) else {}
            node.metadata = {**current, **patch}
            idx.metadata_patched(node)
            with self._head_file_lock(session_log):
                session_log.patch_node(node_id, {}, patch)


    def merge_node_metadata(
        self, session_id: str, node_id: str, patch: dict[str, shared.Any],
    ) -> None:
        """Merge metadata into one persisted node without touching session meta."""
        self.merge_node_metadata_batch(session_id, {node_id: patch})


    def transcript_state(self, session_id: str) -> shared.Optional[tuple]:
        """What a transcript view compares to know the store has not moved."""
        pair = self._open(session_id)
        if pair is None:
            return None
        idx = pair[1]
        return (idx.token, len(idx.nodes_by_seq), idx.next_seq, idx.head_id, idx.shape_version)

    def transcript_appended(self, session_id: str, start: int, stop: int) -> list[dict[str, shared.Any]]:
        """The conversation rows among the nodes stored at positions ``start`` to ``stop``."""
        pair = self._open(session_id)
        if pair is None:
            return []
        nodes = pair[1].nodes_by_seq[start:stop]
        hidden = set(pair[1].meta.get("rewind_hidden_nodes") or [])
        return [shared._node_to_msg(n, session_id) for n in nodes if _is_conversation_node(n, hidden)]

    def transcript_rows(self, session_id: str, ids) -> list[dict[str, shared.Any]]:
        """The conversation rows among these ids; a hidden or unknown id is skipped."""
        pair = self._open(session_id)
        if pair is None:
            return []
        nodes = (pair[1].nodes_by_id.get(row_id) for row_id in ids)
        hidden = set(pair[1].meta.get("rewind_hidden_nodes") or [])
        return [shared._node_to_msg(n, session_id) for n in nodes if n is not None and _is_conversation_node(n, hidden)]

    def transcript_children(self, session_id: str, parent_id: str) -> list[dict[str, shared.Any]]:
        """The conversation rows hanging from ``parent_id`` by either edge."""
        pair = self._open(session_id)
        if pair is None:
            return []
        idx = pair[1]
        hidden = set(pair[1].meta.get("rewind_hidden_nodes") or [])
        ids = dict.fromkeys([*idx.children_by_predecessor.get(parent_id, ()), *idx.children_by_caller.get(parent_id, ())])
        nodes = (idx.nodes_by_id.get(child) for child in ids)
        return [shared._node_to_msg(n, session_id) for n in nodes if n is not None and _is_conversation_node(n, hidden)]

    def get_messages_by_id(self, session_id: str, ids: tuple[str, ...] | list[str]) -> list[dict[str, shared.Any]]:
        """These rows as messages, in the order asked; an unknown id is skipped."""
        pair = self._open(session_id)
        if pair is None:
            return []
        nodes = pair[1].nodes_by_id
        hidden = set(pair[1].meta.get("rewind_hidden_nodes") or [])
        return [shared._node_to_msg(nodes[row_id], session_id) for row_id in ids
                if row_id in nodes and row_id not in hidden]

    def get_messages(self, session_id: str, *, limit: shared.Optional[int] = None) -> list[dict[str, shared.Any]]:
        pair = self._open(session_id)
        if pair is None:
            return []
        _session_log, idx = pair
        hidden = set(pair[1].meta.get("rewind_hidden_nodes") or [])
        msgs = [shared._node_to_msg(n, session_id) for n in idx.all_nodes() if _is_conversation_node(n, hidden)]
        if limit is not None:
            msgs = msgs[-limit:]
        return msgs
