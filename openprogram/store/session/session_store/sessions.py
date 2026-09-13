"""SessionStore sessions operations."""
from __future__ import annotations
from . import shared


class SessionsOperations:
    def create_session(
        self,
        session_id: str,
        agent_id: str,
        *,
        title: str = "",
        source: shared.Optional[str] = None,
        channel: shared.Optional[str] = None,
        peer_display: shared.Optional[str] = None,
        peer_id: shared.Optional[str] = None,
        **other_fields: shared.Any,
    ) -> None:
        # Pull out the project hints BEFORE opening the repo, because
        # they decide WHERE the repo lives (home vs inside a project).
        project_id = other_fields.pop("project_id", None)
        project_path = other_fields.pop("project_path", None)
        # The per-session ``work_dir`` (set by the user via the picker
        # at the top of the chat, stored on the conversation meta) IS
        # the project directory. If the caller didn't pass an explicit
        # ``project_path``, treat ``work_dir`` as the project to bind.
        # NB: we ``get`` (not ``pop``) work_dir — it stays on the meta
        # so ``resolve_work_dir`` keeps reading it for agent file ops.
        if not project_path:
            _wd = other_fields.get("work_dir")
            if isinstance(_wd, str) and _wd.strip():
                project_path = _wd.strip()

        # Bound conversations live under the state root, grouped by
        # project id. Working folders are never the conversation store.
        # An explicitly supplied root is a standalone embedding boundary;
        # with no project hint it must not consult process-wide state.
        if self._explicit_root and not project_path and not project_id:
            project_id = None
        else:
            try:
                from openprogram.store.project import project_store as _projects
                if project_path:
                    proj = _projects.resolve_project(project_path)
                elif project_id and project_id != _projects.DEFAULT_PROJECT_ID:
                    proj = _projects.get_project(project_id)
                    if proj is None:
                        raise ValueError(f"unknown project: {project_id}")
                else:
                    proj = _projects.get_default_project()
                # Isolated callers may intentionally disable the registry's
                # default project; those sessions retain the historical default
                # placement. Explicit bound project resolution failures still
                # propagate below and never fall back silently.
                if proj is None and not project_path and not project_id:
                    project_id = shared._projects_default_id_safe()
                else:
                    project_id = proj.id
                if proj is not None and (not proj.is_default) and proj.path:
                    repo_dir = shared.nested_session_dir(self.root_path, proj.id, session_id)
                    self._record_location(session_id, repo_dir)
                if proj is not None and not proj.is_default:
                    self._project_ids[session_id] = proj.id
            except Exception as e:  # noqa: BLE001 — placement is authoritative
                shared._log.error("project resolution failed for %s: %s", session_id, e)
                raise

        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        git, idx = pair
        if idx.meta.get("id") == session_id:
            return  # Already created
        extra: dict[str, shared.Any] = {}
        if channel:
            extra["channel"] = channel
        if peer_display:
            extra["peer_display"] = peer_display
        if peer_id:
            extra["peer_id"] = peer_id
        for k, v in other_fields.items():
            if v is not None:
                extra[k] = v
        now = shared.time.time()
        # Caller-supplied created_at/updated_at (e.g. channel replay)
        # take precedence over the default ``now``; explicitly pop
        # them from ``extra`` so the **extra spread doesn't collide
        # with the named kwargs.
        created_at = extra.pop("created_at", now)
        updated_at = extra.pop("updated_at", now)

        if project_id:
            extra["project_id"] = project_id

        idx.set_meta(
            id=session_id,
            agent_id=agent_id,
            title=title,
            source=source or "",
            created_at=created_at,
            updated_at=updated_at,
            **extra,
        )
        self._persist_meta(git, idx)

        # Write registry entry.
        _explicit = {"agent_id", "title", "source", "created_at",
                     "updated_at", "channel", "peer_display", "peer_id", "status"}
        self._update_index_entry(
            session_id,
            agent_id=agent_id,
            title=title,
            source=source or "",
            created_at=created_at,
            updated_at=updated_at,
            channel=channel,
            peer_display=peer_display,
            peer_id=peer_id,
            status="idle",
            **{k: v for k, v in extra.items()
               if k in self._INDEX_FIELDS and k not in _explicit},
        )
        self._save_index()

        # Record the reverse index (project → sessions). Also
        # best-effort.
        if project_id:
            try:
                from openprogram.store.project import project_store as _projects
                _projects.bind_session(session_id, project_id)
            except Exception as e:  # noqa: BLE001 — reverse index is best-effort
                shared._log.warning("session %s NOT bound to project %s: %s",
                             session_id, project_id, e)


    def update_session(self, session_id: str, **fields: shared.Any) -> None:
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        git, idx = pair
        with self._head_file_lock(git):
            # head_id needs special routing because it's also the index's
            # ``head_id`` field.
            if "head_id" in fields and fields["head_id"] is not None:
                idx.set_head(fields.pop("head_id"))
            # Drop Nones so we don't clobber existing fields with NULL.
            clean = {k: v for k, v in fields.items() if v is not None}
            if clean:
                idx.set_meta(**clean)
            with idx._persist_lock:
                with idx._lock:
                    meta = dict(idx.meta)
                    meta["head_id"] = idx.head_id
                git.write_meta(meta)
        # Sync registry.
        index_fields = {k: v for k, v in clean.items()
                        if k in self._INDEX_FIELDS}
        if index_fields:
            self._update_index_entry(session_id, **index_fields)
            self._save_index()


    def compare_and_set_session_dict(
        self,
        session_id: str,
        field: str,
        *,
        version: int,
        value: dict[str, shared.Any],
    ) -> bool:
        """Atomically replace one versioned session dictionary."""
        return self.update_session_dict(
            session_id, field,
            lambda current: value if int(current.get("version") or 0) == int(version) else None,
        ) is not None


    def update_session_dict(
        self,
        session_id: str,
        field: str,
        update: shared.Callable[[dict[str, shared.Any]], dict[str, shared.Any] | None],
    ) -> dict[str, shared.Any] | None:
        """Read and transform a dictionary under the session's write lock.

        The callback must not perform I/O or re-enter the store. Returning
        None rejects the update without changing durable or cached state.
        """
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return None
        git, idx = pair
        with self._head_file_lock(git):
            with idx._persist_lock:
                meta = git.read_meta()
                current = meta.get(field)
                value = update(current if isinstance(current, dict) else {})
                if value is None:
                    return None
                meta[field] = dict(value)
                with idx._lock:
                    idx.meta.clear()
                    idx.meta.update(meta)
                    idx.head_id = meta.get("head_id")
                git.write_meta(meta)
        return value


    def get_session(self, session_id: str) -> shared.Optional[dict[str, shared.Any]]:
        pair = self._open(session_id)
        if pair is None:
            return None
        git, idx = pair
        # Synthesize a row-shaped dict so _row_to_session can format it
        # like the old SQLite path.
        meta = dict(idx.meta)
        extra = {k: v for k, v in meta.items() if k not in {
            "id", "title", "agent_id", "source", "model",
            "created_at", "updated_at", "head_id", "last_node_id",
        }}
        row = {
            "id": meta.get("id") or session_id,
            "title": meta.get("title", ""),
            "agent_id": meta.get("agent_id", ""),
            "source": meta.get("source"),
            "model": meta.get("model"),
            "created_at": meta.get("created_at", 0),
            "updated_at": meta.get("updated_at", 0),
            "last_node_id": idx.head_id,
            "extra_json": shared.json.dumps(extra, default=str),
        }
        return shared._row_to_session(row)


    def delete_session(self, session_id: str) -> None:
        with self._session_lock(session_id):
            with shared.session_interprocess_lock(
                session_id, root=self.root_path if self._explicit_root else None,
            ):
                self._delete_session_locked(session_id)


    def _delete_session_locked(self, session_id: str) -> None:
            sdir = self._session_dir(session_id)
            shared.record_delete_intent(self.root_path, session_id, {
                "session_id": session_id, "deleted_at": shared.time.time(),
            })
            with self._lock:
                pair = self._sessions.pop(session_id, None)
                loc = self._locations.get(session_id)
            if pair:
                pair[0].destroy()
            shared.GitSession(sdir).destroy()
            if loc:
                extra = shared.Path(loc)
                if extra != sdir:
                    shared.GitSession(extra).destroy()
            recovery = sdir.parent / ".file-recovery" / session_id
            if recovery.is_dir():
                shared.remove_tree(recovery, ignore_errors=True)
            with self._index_lock:
                if self._index.pop(session_id, None) is not None:
                    self._index_generation += 1
                    self._index_dirty = True
            self._save_index()
            # 位置映射（配对 _record_location）。
            self._forget_location(session_id)
            # 从项目反向索引解绑（配对 bind_session），避免 session_ids 只增不减。
            try:
                from openprogram.store.project import project_store as _projects
                _projects.unbind_session(session_id)
            except Exception as e:  # noqa: BLE001 — reverse index is best-effort
                shared._log.warning("session %s NOT unbound from its project: %s",
                             session_id, e)


    def list_sessions(
        self,
        *,
        agent_id: shared.Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        source: shared.Optional[str] = None,
        include_archived: bool = False,
        **filters: shared.Any,
    ) -> list[dict[str, shared.Any]]:
        """Registry rows, newest activity first.

        Archived sessions are hidden by default — archiving exists so a
        list stops growing without end, which only works if the default
        list honours it. Maintenance passes that must visit every
        session (memory scan, run-state repair, commit lookup) pass
        ``include_archived=True``; an explicit ``archived=`` filter
        selects one side on its own.
        """
        with self._index_lock:
            rows = [dict(row) for row in self._index.values()]
        if agent_id is not None:
            rows = [r for r in rows if r.get("agent_id") == agent_id]
        if source is not None:
            rows = [r for r in rows if r.get("source") == source]
        if not include_archived and "archived" not in filters:
            rows = [r for r in rows if not r.get("archived")]
        for k, v in filters.items():
            if v is not None:
                rows = [r for r in rows if r.get(k) == v]
        rows.sort(key=lambda s: s.get("updated_at") or 0, reverse=True)
        return rows[offset:offset + limit]


    def set_archived(self, session_id: str, archived: bool) -> bool:
        """Flip a session's archive flag. Returns False if unknown.

        Pure metadata: no node is touched and ``updated_at`` stays put
        (index-consistency.html — only appending a message is activity),
        so archiving is fully reversible and never reorders the list.
        """
        with self._index_lock:
            known = session_id in self._index
        if not known and self._open(session_id) is None:
            return False
        self.update_session(session_id, archived=bool(archived))
        return True


    def count_sessions(
        self,
        *,
        agent_id: shared.Optional[str] = None,
        source: shared.Optional[str] = None,
        include_archived: bool = False,
    ) -> int:
        return len(self.list_sessions(
            agent_id=agent_id, source=source,
            include_archived=include_archived, limit=10**9,
        ))

