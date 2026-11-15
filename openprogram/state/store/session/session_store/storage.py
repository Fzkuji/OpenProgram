"""SessionStore storage operations."""
from __future__ import annotations
from . import shared


class StorageOperations:
    @shared.contextmanager
    def reading_cached_index(self):
        """Reads in this block skip the foreign-write check, so they cost no rebuild."""
        token = shared._READING_CACHED_INDEX.set(True)
        try:
            yield
        finally:
            shared._READING_CACHED_INDEX.reset(token)


    def _session_lock(self, session_id: str):
        """This process's lock for the conversation (see ``session_lock``).

        The same lock the conversation's flock takes first, so a thread
        holding the flock always holds this one too, and the two are
        always taken in one order.
        """
        return shared.conversation_lock(
            session_id, self.root_path if self._explicit_root else None,
        )


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

        The per-session lock held by ``_open`` serializes this with
        writers; the recovery scope suppresses only the recursive opens
        needed by its existing HEAD/CAS callbacks.
        """
        if (self, session_id) in shared._REWIND_RECOVERY_SESSIONS.get():
            return
        from .. import rewind_recovery

        rewind_recovery.recover_session_rewinds(session_id, store=self)


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
            if self.root_path.is_dir():
                for project_dir in self.root_path.iterdir():
                    if project_dir.name.startswith(".") or not project_dir.is_dir():
                        continue
                    candidate = shared.nested_session_dir(
                        self.root_path, project_dir.name, session_id)
                    if candidate.is_dir() and shared.session_looks_present(candidate):
                        self._project_ids[session_id] = project_dir.name
                        return candidate
            if recorded:
                return shared.Path(recorded)
            return shared.default_session_dir(self.root_path, session_id)
        proj = None
        lookup_failed = False
        try:
            from openprogram.state.store.project import project_store as _projects
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


    def _open(self, session_id: str, *, create_if_missing: bool = False) -> shared.Optional[tuple[shared.SessionLog, shared.SessionMemoryIndex]]:
        """Return (log, idx). Loads from disk on first access. None if
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
            verified_log: shared.SessionLog | None = None
            sdir = self._session_dir(session_id)
            if create_if_missing and not sdir.exists() and (
                    not self._explicit_root or session_id in self._project_ids):
                try:
                    from openprogram.state.store.project import project_store as _projects
                    from openprogram.state.store.project.location import bound_execution_state
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
                verified_log = shared.SessionLog(sdir)
                if sdir.is_symlink():
                    return None
                if not verified_log.exists():
                    return None
            if cached:
                session_log, idx = cached
                # Agent method runs execute in a fork()'d subprocess
                # that appends history and moves HEAD on disk directly —
                # this process's cached index can't see that, so a
                # mid-run load_session answered from stale memory (the
                # transcript kept showing the previous run until turn
                # end). Two stat calls detect the foreign write; rebuild
                # from disk when it happened. Own-process writes call
                # mark_synced(), so they never trigger a rebuild.
                if (not shared._READING_CACHED_INDEX.get()
                        and self._absorb_foreign_writes(session_log, idx)):
                    shared.report_foreign_change(session_id)
                # A cached session may be the callback target of an active
                # CheckpointStore transaction (its get_head/CAS callbacks
                # re-enter SessionStore while the intent lock is held).  The
                # first public open after a process/session cache miss is the
                # restart boundary that needs lazy replay; explicit rewind
                # entry points also recover before planning or applying.
                return cached
            if not sdir.exists() and not create_if_missing:
                return None
            session_log = verified_log or shared.SessionLog(sdir)
            idx = shared.SessionMemoryIndex()
            if session_log.exists():
                fingerprint = session_log.stat_fingerprint()
                idx.rebuild(
                    session_log.list_history(),
                    session_log.read_meta(),
                    shared._node_conv_predecessor,
                    shared._node_caller,
                )
                session_log.mark_synced(fingerprint)
            with self._lock:
                # New key lands at the MRU (end) of the OrderedDict.
                self._sessions[session_id] = (session_log, idx)
                # Evict least-recently-used entries beyond the cap. The
                # just-inserted session is at the MRU end, so popitem(last=
                # False) (oldest) never evicts it as long as cap >= 1. An
                # evicted index rebuilds losslessly from the log on next access;
                # an in-flight turn keeps its own (log, idx) reference and is
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
                    if self._sessions.get(session_id) == (session_log, idx):
                        self._sessions.pop(session_id, None)
                raise
            return session_log, idx


    def _absorb_foreign_writes(self, session_log: shared.SessionLog, idx: shared.SessionMemoryIndex) -> bool:
        """Bring the cached index up to date with writes from other processes; True when it rebuilt."""
        with idx._persist_lock:
            if not session_log.exists() or not session_log.stale():
                return False
            # Rebuild IN PLACE (reset + repopulate the SAME index
            # object): callers across a multi-step operation hold
            # (log, idx) from an earlier _open — swapping the
            # cached object would orphan their reference, and a
            # later step that re-opens (e.g. commit_turn) would
            # persist the rebuilt object's stale head over their
            # in-memory update.
            # A foreign atomic write can finish during this load.
            # Keep the pre-read stamp so the next read detects it.
            fingerprint = session_log.stat_fingerprint()
            disk_meta = session_log.read_meta()
            disk_nodes = session_log.list_history()
            # A concurrent reader can land here between
            # create_session's set_meta() and its _persist_meta()
            # — the repo exists (some other write initialized it)
            # but its fields are still empty on disk. Blindly
            # rebuilding then resets the index and throws away the
            # just-populated in-memory meta (source/channel/peer_*
            # silently vanished). Disk with nothing in it is never
            # a more truthful view than populated memory, so skip
            # the rebuild instead of destroying state.
            if not disk_meta and not disk_nodes and (idx.meta or idx.head_id):
                return False
            idx.rebuild(
                disk_nodes,
                disk_meta,
                shared._node_conv_predecessor,
                shared._node_caller,
            )
            session_log.mark_synced(fingerprint)
            return True


    def read_index(self, session_id: str, reader):
        """Answer ``reader(index)`` from the cached index, or None for no session.

        The reader gets the live index: it must treat it as read-only and
        tolerate a concurrent write, because no lock is held while it runs.
        """
        pair = self._open(session_id)
        return reader(pair[1]) if pair else None


    def _conversation_write_lock(self, session_id: str, *, timeout: shared.Optional[float] = None):
        """The conversation's write lock: its process lock, then its flock."""
        return shared.session_interprocess_lock(
            session_id, timeout=timeout,
            root=self.root_path if self._explicit_root else None,
        )


    @shared.contextmanager
    def _head_file_lock(self, session_log: shared.SessionLog):
        """Serialize durable writers by session id, then re-read placement."""
        session_id = session_log.path.name
        with self._conversation_write_lock(session_id):
            if shared.is_deleted(self.root_path, session_id):
                raise RuntimeError(f"session deleted: {session_id}")
            current = self._session_dir(session_id)
            if current != session_log.path:
                session_log.path = current
            yield


    def _persist_meta(self, session_log: shared.SessionLog, idx: shared.SessionMemoryIndex) -> None:
        """Sync the in-memory meta back to the session log. Called whenever
        title / head_id / extra change. Branch records are not written
        from the cache (``_meta_for_write``); ``_update_meta`` writes them."""
        with self._head_file_lock(session_log):
            with idx._persist_lock:
                session_log.write_meta(self._meta_for_write(session_log, idx))


    def _meta_for_write(self, session_log: shared.SessionLog, idx: shared.SessionMemoryIndex,
                        durable: shared.Optional[dict] = None) -> dict:
        """The cached meta to write back whole, with the branch records
        as they are on disk.

        The caller holds the session write lock and ``idx._persist_lock``.
        Branch records (``BRANCH_RECORD_KEYS``) are written only on the
        record read inside that lock (``_update_meta`` and the other
        read-modify-write paths), so the durable copy is never older than
        the cached one; taking it here
        keeps a cache that missed another process's write (or a write
        that landed after this one's caller read it) from putting an
        old copy of the records back. A caller that has just changed the
        records on the record it read inside the lock passes it as
        ``durable``.
        """
        if durable is None:
            durable = session_log.read_meta()
        with idx._lock:
            shared.take_durable_branch_records(idx.meta, durable)
            meta = dict(idx.meta)
            meta["head_id"] = idx.head_id
        return meta


    def _update_meta(
        self,
        session_log: shared.SessionLog,
        idx: shared.SessionMemoryIndex,
        mutate,
        *,
        keys: tuple[str, ...] = ("branches",),
    ) -> shared.Any:
        """Read-modify-write the durable session meta under the write lock.

        ``mutate(meta)`` changes the fields it owns in the meta read from
        disk inside the lock and returns ``(write, value)``; nothing is
        written when ``write`` is false. The written ``keys`` are then
        copied into the cached meta (``head_id`` moves the cached head),
        leaving the rest of the cache alone. Every branch-record writer
        goes through here, so a writer never replaces a record with a
        copy it read earlier.
        """
        with self._head_file_lock(session_log):
            with idx._persist_lock:
                meta = session_log.read_meta()
                write, value = mutate(meta)
                if not write:
                    return value
                session_log.write_meta(meta)
                with idx._lock:
                    for key in keys:
                        if key == "head_id":
                            idx.head_id = meta.get("head_id")
                        if key in meta:
                            idx.meta[key] = meta[key]
                        else:
                            idx.meta.pop(key, None)
                return value


    def session_workdir(self, session_id: str) -> shared.Optional[shared.Path]:
        """Path of the per-session scratch workdir, materialized on first
        write. None if the session doesn't exist yet."""
        pair = self._open(session_id)
        if not pair:
            return None
        session_log, _ = pair
        session_log._ensure_init()
        return session_log.workdir_path


    def commit_turn(self, session_id: str, message: str) -> shared.Optional[str]:
        """Make the session's fields durable at a turn boundary. Nodes are
        already durable; ``message`` names the boundary for callers' logs."""
        pair = self._open(session_id)
        if not pair:
            return None
        session_log, idx = pair
        with self._head_file_lock(session_log):
            with idx._persist_lock:
                session_log.write_meta(self._meta_for_write(session_log, idx))
            return None
