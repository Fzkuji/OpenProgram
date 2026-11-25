"""SessionStore sessions operations."""
from __future__ import annotations
from openprogram.state import execution_facts

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
                from openprogram.state.store.project import project_store as _projects
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
                    self._adopt_unclaimed_repo(session_id, repo_dir)
                    self._record_location(session_id, repo_dir)
                if proj is not None and not proj.is_default:
                    self._project_ids[session_id] = proj.id
            except Exception as e:  # noqa: BLE001 — placement is authoritative
                shared._log.error("project resolution failed for %s: %s", session_id, e)
                raise

        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        session_log, idx = pair
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
        from ..branch_config import sync_active_config
        sync_active_config(idx.meta, session_id)
        self._persist_meta(session_log, idx)

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
                from openprogram.state.store.project import project_store as _projects
                _projects.bind_session(session_id, project_id)
            except Exception as e:  # noqa: BLE001 — reverse index is best-effort
                shared._log.warning("session %s NOT bound to project %s: %s",
                             session_id, project_id, e)


    def _adopt_unclaimed_repo(self, session_id: str, target: shared.Path) -> None:
        """Move a draft's settings-only repo to the project's placement.

        Settings chosen before the first message (a model pick, say)
        reach ``update_session``, which materialises the repo at the
        default location with no ``id`` and no history. Left there, the
        project's create would keep opening that repo and the
        conversation would stay outside its project. A repo that already
        holds a created conversation is never moved here.
        """
        with self._session_lock(session_id):
            current = self._session_dir(session_id)
            if current == target or target.exists() or not current.is_dir():
                return
            session_log = shared.SessionLog(current)
            if session_log.read_meta().get("id") or session_log.list_history():
                return
            with self._lock:
                self._sessions.pop(session_id, None)
            target.parent.mkdir(parents=True, exist_ok=True)
            shared.move_session_folder(current, target)


    def assign_project(self, session_id: str, project_id: str) -> None:
        """Make ``project_id`` the conversation's one project.

        Records it on the meta and in the registry (dropping the binding to
        any other project). A conversation with no turns yet also moves
        into the new project's ``chats/`` folder; one with turns stays
        where it is, found through the location map.
        """
        from openprogram.state.store.project import project_store as _projects

        project = _projects.get_project(project_id)
        if project is None:
            raise ValueError(f"unknown project: {project_id}")
        self.update_session(session_id, project_id=project_id)
        _projects.unbind_session(session_id)
        _projects.bind_session(session_id, project_id)
        target = shared.target_dir_for_project(
            self.root_path, session_id,
            project_id=project.id, is_default=bool(project.is_default),
        )
        with self._session_lock(session_id):
            with shared.session_interprocess_lock(
                session_id, timeout=15.0,
                root=self.root_path if self._explicit_root else None,
            ):
                current = self._session_dir(session_id)
                if (current == target or target.exists() or not current.is_dir()
                        or shared.SessionLog(current).list_history()):
                    if project.is_default:
                        self._project_ids.pop(session_id, None)
                    else:
                        self._project_ids[session_id] = project.id
                    return
                with self._lock:
                    self._sessions.pop(session_id, None)
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shared.move_session_folder(current, target)
                except shared.SessionFolderBusy as busy:
                    # The binding above is what makes it the project's
                    # conversation. Its folder stays where it is, as the
                    # folder of one with turns does, and the location map
                    # names it.
                    shared._log.warning("session %s kept its folder: %s", session_id, busy)
                    target = current
                if project.is_default:
                    self._project_ids.pop(session_id, None)
                else:
                    self._project_ids[session_id] = project.id
        self._record_location(session_id, target)


    def move_session(self, session_id: str, project_id: str) -> dict[str, shared.Any]:
        """Move a conversation, turns and all, into another project.

        The repository and its file-recovery tree go to the project's
        ``chats/`` folder, the meta and the registry name the new project,
        and the project it leaves is appended to the meta's
        ``project_history`` so that project's memory, which already holds
        the earlier turns, is not written them again. Returns the old and
        new project ids and whether anything moved; a conversation already
        in the project is left alone.
        """
        from openprogram.state.store.project import project_store as _projects

        project = _projects.get_project(project_id)
        if project is None:
            raise ValueError(f"unknown project: {project_id}")
        current = _projects.project_for_session(session_id) or _projects.get_default_project()
        if current is not None and current.id == project.id:
            return {"from_project_id": current.id, "project_id": project.id, "moved": False}
        target = shared.target_dir_for_project(
            self.root_path, session_id,
            project_id=project.id, is_default=bool(project.is_default),
        )
        with self._session_lock(session_id):
            with shared.session_interprocess_lock(
                session_id, timeout=15.0,
                root=self.root_path if self._explicit_root else None,
            ):
                source = self._session_dir(session_id)
                if not shared.session_looks_present(source):
                    raise ValueError(f"no such session: {session_id}")
                # An earlier move that could neither finish nor undo left
                # the folder at the destination with the binding on the
                # old project. This call finishes that move.
                resumed = source == target
                if resumed:
                    home = shared.target_dir_for_project(
                        self.root_path, session_id,
                        project_id=current.id if current is not None else None,
                        is_default=bool(current.is_default) if current is not None else True,
                    )
                else:
                    if target.exists():
                        raise ValueError(f"destination already exists: {target}")
                    with self._lock:
                        self._sessions.pop(session_id, None)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shared.move_session_folder(source, target)
                    home = source
                recovery = home.parent / ".file-recovery" / session_id
                recovery_target = target.parent / ".file-recovery" / session_id
                if recovery != recovery_target and recovery.is_dir():
                    recovery_target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        self._move_recovery_tree(recovery, recovery_target)
                    except OSError:
                        if not resumed:
                            # Both folders move or neither: put the conversation back.
                            self._return_session_folder(session_id, target, source, recovery)
                        raise
                if project.is_default:
                    self._project_ids.pop(session_id, None)
                else:
                    self._project_ids[session_id] = project.id
                self._record_location(session_id, target)
        _projects.unbind_session(session_id)
        _projects.bind_session(session_id, project.id)
        history = list((self.get_session(session_id) or {}).get("project_history") or [])
        if current is not None:
            history.append(current.id)
        self.update_session(session_id, project_id=project.id, project_history=history)
        return {"from_project_id": current.id if current else None, "project_id": project.id, "moved": True}


    @staticmethod
    def _move_recovery_tree(recovery: shared.Path, target: shared.Path) -> None:
        """Rename a file-recovery tree beside its conversation.

        Turns taken while the tree was stranded have started a tree at the
        destination; the stranded turns then move in one by one beside them.
        """
        if not target.exists():
            shared.move_session_folder(recovery, target)
            return
        for entry in sorted(recovery.iterdir()):
            if not (target / entry.name).exists():
                shared.move_session_folder(entry, target / entry.name)
        try:
            recovery.rmdir()
        except OSError:
            pass


    def _return_session_folder(self, session_id: str, moved: shared.Path, home: shared.Path,
                               recovery: shared.Path) -> None:
        """Undo a conversation's move after its file-recovery tree stayed behind."""
        try:
            shared.move_session_folder(moved, home)
        except OSError as error:
            # It cannot go back either. Record where it is so it stays
            # readable, and say where each half is.
            self._record_location(session_id, moved)
            raise shared.SessionFolderBusy(
                f"the conversation is now at {moved}, but its file-recovery folder {recovery} is in use "
                "by another program and stayed behind; close what has files in it open, then move the "
                "conversation again"
            ) from error


    def is_created(self, session_id: str) -> bool:
        """True once ``create_session`` has written this conversation.

        A repo can exist before that: settings picked while the chat was
        still a draft are stored ahead of it, without an ``id``. A repo
        that already has history counts as created even without one.
        """
        meta = self.read_disk_meta(session_id)
        if meta is None:
            return False
        if meta.get("id"):
            return True
        return bool(shared.SessionLog(self._session_dir(session_id)).list_history())


    def ensure_branch_config(self, session_id: str) -> shared.Optional[dict[str, shared.Any]]:
        """Materialize legacy settings once before a client binds a branch write."""
        pair = self._open(session_id)
        if pair is None:
            return None
        session_log, idx = pair
        from ..branch_config import sync_active_config
        with self._head_file_lock(session_log):
            meta = session_log.read_meta()
            active = meta.get("active_branch_id")
            if not active or "config" not in (meta.get("branch_refs") or {}).get(active, {}):
                sync_active_config(meta, session_id)
                with idx._persist_lock:
                    with idx._lock:
                        idx.meta = meta
                        idx.head_id = meta.get("head_id")
                    session_log.write_meta(meta)
        return self.get_session(session_id)


    def update_session(self, session_id: str, **fields: shared.Any) -> None:
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return
        session_log, idx = pair
        from ..branch_config import (
            BRANCH_CONFIG_FIELDS, BRANCH_UNSET, advance_branch, behind_selected_tip,
            check_branch, sync_active_config,
        )
        expected_branch = fields.pop("expected_branch_id", BRANCH_UNSET)
        # A turn recording its reply as the head when it ends: skipped when
        # the branch has already carried on past that reply.
        forward_only = bool(fields.pop("head_forward_only", False))
        with self._head_file_lock(session_log):
            meta = session_log.read_meta()
            check_branch(meta, expected_branch)
            clean = {k: v for k, v in fields.items() if v is not None}
            if "head_id" in clean:
                target = clean.pop("head_id")
                if not (forward_only and behind_selected_tip(meta, target, idx.nodes_by_id)):
                    advance_branch(meta, session_id, target, idx.nodes_by_id)
            meta.update(clean)
            if any(key in clean for key in BRANCH_CONFIG_FIELDS):
                sync_active_config(meta, session_id)
            with idx._persist_lock:
                with idx._lock:
                    idx.meta = meta
                    idx.head_id = meta.get("head_id")
                session_log.write_meta(meta)
        # Sync registry.
        index_fields = {k: v for k, v in clean.items()
                        if k in self._INDEX_FIELDS}
        if index_fields:
            self._update_index_entry(session_id, **index_fields)
            self._save_index()


    def update_branch_config(
        self,
        session_id: str,
        branch_id: shared.Optional[str],
        values: dict[str, shared.Any],
        **guards: shared.Any,
    ) -> dict[str, shared.Any] | None:
        """Write one branch's settings under the session meta lock.

        ``branch_id`` names the branch whatever is selected; ``None`` is the
        selected one. A ``None`` value puts that setting back to inherit.
        Raises ``BranchConfigConflict`` (``branch_gone``) for a branch with
        no record, or (``branch_changed``) when an ``expected_branch_id``
        guard no longer names the selected branch. Returns the branch's
        settings after the write.
        """
        # A draft's first setting (a model pick) creates its repo, as
        # ``update_session`` does.
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return None
        session_log, idx = pair
        from ..branch_config import BRANCH_UNSET, check_branch, config_for_branch, write_branch_config
        with self._head_file_lock(session_log):
            meta = session_log.read_meta()
            check_branch(meta, guards.get("expected_branch_id", BRANCH_UNSET))
            selected = write_branch_config(meta, session_id, branch_id, values)
            with idx._persist_lock:
                with idx._lock:
                    idx.meta = meta
                    idx.head_id = meta.get("head_id")
                session_log.write_meta(meta)
        if selected:
            index_fields = {k: v for k, v in values.items() if k in self._INDEX_FIELDS and v is not None}
            if "harness" in values or "model_override" in values:
                # The sidebar's agent mark: what the selected branch runs in now.
                current = config_for_branch(meta, meta.get("active_branch_id"))
                index_fields["harness"] = current.get("harness")
                index_fields["model_override"] = current.get("model_override")
            if index_fields:
                self._update_index_entry(session_id, **index_fields)
                self._save_index()
        return config_for_branch(meta, meta.get("active_branch_id") if selected else branch_id)


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
        **guards: shared.Any,
    ) -> dict[str, shared.Any] | None:
        """Read and transform a dictionary under the session's write lock.

        The callback must not perform I/O or re-enter the store. Returning
        None rejects the update without changing durable or cached state.
        """
        pair = self._open(session_id, create_if_missing=True)
        if pair is None:
            return None
        session_log, idx = pair
        with self._head_file_lock(session_log):
            with idx._persist_lock:
                meta = session_log.read_meta()
                from ..branch_config import (
                    BRANCH_CONFIG_FIELDS, BRANCH_UNSET, check_branch, config_for_branch,
                    sync_active_config, write_branch_config,
                )
                check_branch(meta, guards.get("expected_branch_id", BRANCH_UNSET))
                # ``branch_id`` names the branch a branch setting belongs to,
                # whatever is selected; omitted is the selected one.
                branch_id = guards.get("branch_id")
                named = (field in BRANCH_CONFIG_FIELDS and branch_id
                         and branch_id != meta.get("active_branch_id"))
                current = (config_for_branch(meta, branch_id) if named else meta).get(field)
                value = update(current if isinstance(current, dict) else {})
                if value is None:
                    return None
                if named:
                    write_branch_config(meta, session_id, branch_id, {field: dict(value)})
                else:
                    meta[field] = dict(value)
                    if field in BRANCH_CONFIG_FIELDS:
                        sync_active_config(meta, session_id)
                with idx._lock:
                    idx.meta.clear()
                    idx.meta.update(meta)
                    idx.head_id = meta.get("head_id")
                session_log.write_meta(meta)
        return value


    def clear_session_field(
        self,
        session_id: str,
        field: str,
        *,
        when: shared.Callable[[dict[str, shared.Any]], bool],
    ) -> bool:
        """Remove one session field if ``when(meta)`` holds under the session's write lock.

        ``update_session`` cannot do this: it drops ``None`` values rather than
        writing them. The callback must not perform I/O or re-enter the store.
        """
        pair = self._open(session_id)
        if pair is None:
            return False
        git, idx = pair
        with self._head_file_lock(git):
            with idx._persist_lock:
                meta = git.read_meta()
                if field not in meta or not when(meta):
                    return False
                del meta[field]
                from ..branch_config import BRANCH_CONFIG_FIELDS, sync_active_config
                if field in BRANCH_CONFIG_FIELDS:
                    sync_active_config(meta, session_id)
                with idx._lock:
                    idx.meta.clear()
                    idx.meta.update(meta)
                    idx.head_id = meta.get("head_id")
                git.write_meta(meta)
        return True


    def read_disk_meta(self, session_id: str) -> shared.Optional[dict[str, shared.Any]]:
        """The session fields exactly as they are on disk now, or ``None`` when the
        session has none.

        For reads that must see another process's latest session-level
        write (a Goal changed from another surface) without paying for
        ``invalidate_cache``, which re-reads every history node. The
        in-memory index is neither consulted nor changed.
        """
        if (
            not isinstance(session_id, str)
            or not session_id
            or session_id in {".", ".."}
            or "/" in session_id
            or "\\" in session_id
        ):
            return None
        session_dir = self._session_dir(session_id)
        session_log = shared.SessionLog(session_dir)
        if not session_log.exists():
            return None
        return session_log.read_meta()


    def get_session(self, session_id: str) -> shared.Optional[dict[str, shared.Any]]:
        pair = self._open(session_id)
        if pair is None:
            return None
        session_log, idx = pair
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
                # Still under the conversation's lock: the lock files go
                # last, after the delete mark that turns away whoever takes
                # the lock next.
                shared.remove_session_lock_files(
                    session_id, self.root_path if self._explicit_root else None)


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
            shared.SessionLog(sdir).destroy()
            execution_facts.forget_session_runs(session_id)
            if loc:
                extra = shared.Path(loc)
                if extra != sdir:
                    shared.SessionLog(extra).destroy()
            recovery = sdir.parent / ".file-recovery" / session_id
            if recovery.is_dir():
                shared.remove_tree(recovery, ignore_errors=True)
            if not self._explicit_root:
                # The shadow repositories live beside the default store only.
                try:
                    from openprogram.state.store.shadow_git.store import release_session
                    release_session(session_id)
                except Exception:  # noqa: BLE001 — the start-up sweep takes it
                    shared._log.warning("file history of %s not released", session_id,
                                        exc_info=True)
            with self._index_lock:
                if self._index.pop(session_id, None) is not None:
                    self._index_generation += 1
                    self._index_dirty = True
            self._save_index()
            # 位置映射（配对 _record_location）。
            self._forget_location(session_id)
            # 从项目反向索引解绑（配对 bind_session），避免 session_ids 只增不减。
            try:
                from openprogram.state.store.project import project_store as _projects
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

