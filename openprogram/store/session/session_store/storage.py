"""SessionStore storage operations."""
from __future__ import annotations
from . import shared


class StorageOperations:
    def _session_lock(self, session_id: str):
        with self._lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = shared.threading.RLock()
                self._session_locks[session_id] = lock
            return lock


    @shared.contextmanager
    def _rewind_recovery_scope(self, session_id: str):
        active = shared._REWIND_RECOVERY_SESSIONS.get()
        key = (self, session_id)
        token = shared._REWIND_RECOVERY_SESSIONS.set(active | {key})
        try:
            yield
        finally:
            shared._REWIND_RECOVERY_SESSIONS.reset(token)


    def _recover_session_rewinds_before_exposure(self, session_id: str) -> None:
        """Replay this session's pending rewind journal before returning it.

        The recovery implementation is imported lazily to keep the session
        store independent from the agent layer at module import time.  The
        per-session lock held by ``_open`` serializes this with writers; the
        recovery scope suppresses only the recursive opens needed by its
        existing HEAD/CAS callbacks.
        """
        if (self, session_id) in shared._REWIND_RECOVERY_SESSIONS.get():
            return
        from openprogram.agent._rewind import recover_session_rewinds

        recover_session_rewinds(session_id, store=self)


    def _session_dir(self, session_id: str) -> shared.Path:
        """Current readable or create placement for ``session_id``.

        Prefers application-owned nested/default layouts. Stale workdir
        caches are not rewritten into a replacement folder. Deleted
        sessions are not resurrected.
        """
        locations = self._load_locations()
        with self._lock:
            for key, value in locations.items():
                self._locations[key] = value
            loc_map = dict(self._locations)
        if self._explicit_root:
            recorded = loc_map.get(session_id)
            project_id = self._project_ids.get(session_id)
            recorded_path = shared.Path(recorded) if recorded else None
            # A durable recorded path wins while it is still readable. If it
            # is stale, search application-owned nested storage before
            # returning the stale migration candidate.
            existing_locations = (
                {session_id: str(recorded_path)}
                if recorded_path is not None and recorded_path.is_dir()
                else {}
            )
            existing = shared.resolve_existing_dir(
                self.root_path, session_id, locations=existing_locations,
                project_id=project_id, is_default=not bool(project_id),
            )
            if existing is not None:
                return existing
            if project_id:
                return shared.nested_session_dir(self.root_path, project_id, session_id)
            # A fresh store can rebuild placement from the nested directory
            # even if locations.json and the summary index are unavailable.
            projects_root = self.root_path / "projects"
            if projects_root.is_dir():
                for project_dir in projects_root.iterdir():
                    candidate = project_dir / session_id
                    if candidate.is_dir() and shared.session_looks_present(candidate):
                        self._project_ids[session_id] = project_dir.name
                        return candidate
            if recorded:
                return shared.Path(recorded)
            return shared.default_session_dir(self.root_path, session_id)
        proj = None
        lookup_failed = False
        try:
            from openprogram.store.project import project_store as _projects
            proj = _projects.project_for_session(session_id)
        except Exception:
            lookup_failed = True
            if session_id not in loc_map:
                raise shared.SessionPlacementError(
                    f"could not resolve project placement for session {session_id}"
                ) from None
            shared._log.warning(
                "project lookup failed for %s; using durable location mapping",
                session_id,
                exc_info=True,
            )
        existing = shared.resolve_existing_dir(
            self.root_path, session_id,
            locations=loc_map,
            project_id=None if proj is None else proj.id,
            is_default=True if proj is None else bool(proj.is_default),
            project_path=None if proj is None else proj.path,
        )
        if existing is not None:
            return existing
        recorded = loc_map.get(session_id)
        if recorded:
            return shared.Path(recorded)
        if lookup_failed:
            raise shared.SessionPlacementError(
                f"could not resolve project placement for session {session_id}"
            )
        return shared.target_dir_for_project(
            self.root_path, session_id,
            project_id=None if proj is None else proj.id,
            is_default=True if proj is None else bool(proj.is_default),
        )


    def _open(self, session_id: str, *, create_if_missing: bool = False) -> shared.Optional[tuple[shared.GitSession, shared.SessionMemoryIndex]]:
        """Return (git, idx). Loads from disk on first access. None if
        session doesn't exist and ``create_if_missing`` is False."""
        if not create_if_missing and (
            not isinstance(session_id, str)
            or not session_id
            or session_id in {".", ".."}
            or "/" in session_id
            or "\\" in session_id
        ):
            return None
        if create_if_missing and shared.is_deleted(self.root_path, session_id):
            return None
        with self._session_lock(session_id):
            # Recheck the durable tombstone after acquiring the same
            # per-session lock used by delete_session. This closes the race
            # where a stale writer passed the early check while deletion was
            # publishing its intent.
            if create_if_missing and shared.is_deleted(self.root_path, session_id):
                return None
            verified_git: shared.GitSession | None = None
            sdir = self._session_dir(session_id)
            if create_if_missing and not sdir.exists() and (
                    not self._explicit_root or session_id in self._project_ids):
                try:
                    from openprogram.store.project import project_store as _projects
                    from openprogram.store.project.location import bound_execution_state
                    project = (_projects.get_project(self._project_ids[session_id])
                               if self._explicit_root
                               else _projects.project_for_session(session_id))
                    if project is not None and not getattr(project, "is_default", False):
                        if bound_execution_state(project) is not None:
                            return None
                except Exception:
                    shared._log.error("project location validation failed for %s",
                               session_id, exc_info=True)
                    return None
            with self._lock:
                cached = self._sessions.get(session_id)
                if cached and cached[0].path != sdir:
                    # Another process relocated the session since it was opened.
                    self._sessions.pop(session_id)
                    cached = None
                if cached:
                    self._sessions.move_to_end(session_id)
            if not create_if_missing:
                verified_git = shared.GitSession(sdir)
                if (
                    sdir.is_symlink()
                    or not verified_git.exists()
                    or not (sdir / "history").is_dir()
                ):
                    return None
            if cached:
                git, idx = cached
                # @agentic_function runs execute in a fork()'d subprocess
                # that appends history and moves HEAD on disk directly —
                # this process's cached index can't see that, so a
                # mid-run load_session answered from stale memory (the
                # transcript kept showing the previous run until turn
                # end). Two stat calls detect the foreign write; rebuild
                # from disk when it happened. Own-process writes call
                # mark_synced(), so they never trigger a rebuild.
                with idx._persist_lock:
                    if git.exists() and git.stale():
                        # Rebuild IN PLACE (reset + repopulate the SAME index
                        # object): callers across a multi-step operation hold
                        # (git, idx) from an earlier _open — swapping the
                        # cached object would orphan their reference, and a
                        # later step that re-opens (e.g. commit_turn) would
                        # persist the rebuilt object's stale head over their
                        # in-memory update.
                        disk_meta = git.read_meta()
                        disk_hist = git.list_history()
                        # A concurrent reader can land here between
                        # create_session's set_meta() and its _persist_meta()
                        # — the repo exists (some other write initialized it)
                        # but meta.json is still empty/absent on disk. Blindly
                        # rebuilding then resets the index and throws away the
                        # just-populated in-memory meta (source/channel/peer_*
                        # silently vanished). Disk with nothing in it is never
                        # a more truthful view than populated memory, so skip
                        # the rebuild instead of destroying state.
                        if not disk_meta and not disk_hist and (idx.meta or idx.head_id):
                            pass
                        else:
                            idx.rebuild_from_paths(
                                disk_hist,
                                disk_meta,
                                shared._node_conv_predecessor,
                                shared._node_caller,
                            )
                            git.mark_synced()
                # A cached session may be the callback target of an active
                # CheckpointStore transaction (its get_head/CAS callbacks
                # re-enter SessionStore while the intent lock is held).  The
                # first public open after a process/session cache miss is the
                # restart boundary that needs lazy replay; explicit rewind
                # entry points also recover before planning or applying.
                return cached
            if not sdir.exists() and not create_if_missing:
                return None
            git = verified_git or shared.GitSession(sdir)
            idx = shared.SessionMemoryIndex()
            if git.exists():
                idx.rebuild_from_paths(
                    git.list_history(),
                    git.read_meta(),
                    shared._node_conv_predecessor,
                    shared._node_caller,
                )
                git.mark_synced()
            with self._lock:
                # New key lands at the MRU (end) of the OrderedDict.
                self._sessions[session_id] = (git, idx)
                # Evict least-recently-used entries beyond the cap. The
                # just-inserted session is at the MRU end, so popitem(last=
                # False) (oldest) never evicts it as long as cap >= 1. An
                # evicted index rebuilds losslessly from git on next access;
                # an in-flight turn keeps its own (git, idx) reference and is
                # unaffected by being dropped from this dict.
                while len(self._sessions) > self._cache_cap:
                    self._sessions.popitem(last=False)
            try:
                self._recover_session_rewinds_before_exposure(session_id)
            except BaseException:
                # Do not publish a session whose recovery did not finish.  A
                # later public open must retry recovery instead of returning
                # the partially exposed cached index.
                with self._lock:
                    if self._sessions.get(session_id) == (git, idx):
                        self._sessions.pop(session_id, None)
                raise
            return git, idx


    @shared.contextmanager
    def _head_file_lock(self, git: shared.GitSession):
        """Serialize durable writers by session id, then re-read placement."""
        session_id = git.path.name
        with shared.session_interprocess_lock(
            session_id, root=self.root_path if self._explicit_root else None,
        ):
            if shared.is_deleted(self.root_path, session_id):
                raise RuntimeError(f"session deleted: {session_id}")
            current = self._session_dir(session_id)
            if current != git.path:
                git.path = current
            yield


    def _persist_meta(self, git: shared.GitSession, idx: shared.SessionMemoryIndex) -> None:
        """Sync the in-memory meta back to ``meta.json``. Called whenever
        title / head_id / extra / branches change."""
        with self._head_file_lock(git):
            with idx._persist_lock:
                with idx._lock:
                    meta = dict(idx.meta)
                    meta["head_id"] = idx.head_id
                git.write_meta(meta)


    def session_workdir(self, session_id: str) -> shared.Optional[shared.Path]:
        """Path of the per-session scratch workdir, materialized on first
        write. None if the session doesn't exist yet."""
        pair = self._open(session_id)
        if not pair:
            return None
        git, _ = pair
        git._ensure_init()
        return git.workdir_path


    def commit_turn(self, session_id: str, message: str) -> shared.Optional[str]:
        """Commit the current working tree as one turn. Public so the
        dispatcher can call it at turn end; also called internally by
        write paths that don't need an explicit commit boundary.

        Repo layout is append-only by design — no mutable "current
        state" mirror file: history/ holds per-node files, context/
        commits/ holds per-commit files, meta.json carries session-
        level scalars (head_id is a UI pointer, single-valued by
        construction). Two agents writing concurrently never target
        the same file. Refresh meta only here.

        Returns commit sha or None if nothing to commit.
        """
        pair = self._open(session_id)
        if not pair:
            return None
        git, idx = pair
        with self._head_file_lock(git):
            with idx._persist_lock:
                with idx._lock:
                    meta = dict(idx.meta)
                    meta["head_id"] = idx.head_id
                git.write_meta(meta)
            return git.commit_all(message)

