"""SessionStore index operations."""
from __future__ import annotations
from . import shared


# A journal row in any other stage means the session may be mid-move.
_MIGRATION_SETTLED = frozenset({"done", "deleted"})

# Any of these in a session directory is user data worth keeping in place.
_SESSION_DATA = ("main.jsonl", "agents", "events.jsonl")


class IndexOperations:
    def _locations_path(self) -> shared.Path:
        return self.root_path / shared.LOCATIONS_FILE


    def _load_locations(self) -> dict[str, str]:
        p = self._locations_path()
        if not p.exists():
            return {}
        try:
            data = shared.json.loads(shared.read_text_with_retry(p))
            if not isinstance(data, dict):
                return {}
            from ..placement import rooted_location
            return {str(k): str(rooted_location(self.root_path, v)) for k, v in data.items()}
        except (OSError, shared.json.JSONDecodeError):
            return {}


    def _save_locations(self, locations: dict[str, str]) -> None:
        shared.atomic_write_text(
            self._locations_path(),
            shared.json.dumps(locations, indent=2, ensure_ascii=False),
        )


    def _record_location(self, session_id: str, repo_dir: shared.Path) -> None:
        """Persist that ``session_id``'s repo lives at ``repo_dir`` (an
        absolute path outside the home root). Idempotent."""
        with self._session_lock(session_id), shared.registry_file_lock(self.root_path, "locations"):
            snapshot = self._load_locations()
            snapshot[session_id] = str(repo_dir)
            self._save_locations(snapshot)
            with self._lock:
                self._locations.update(snapshot)


    def relocate_project_sessions(
        self, session_ids, new_project_path, project_id=None, old_path=None,
    ) -> int:
        """Keep conversation placement under application storage.

        Nested home-owned sessions do not move. Unmigrated workdir
        copies are recorded in the migration journal and the location
        index is updated only after that durable repair, never as a
        best-effort rewrite.
        """
        from ..placement import legacy_project_session_dir
        from ..migration import load_journal, update_journal_row

        base = shared.Path(new_project_path).expanduser()
        moved = 0
        root = self.root_path
        journal = load_journal(root)
        for sid in session_ids or []:
            with self._session_lock(sid):
                with shared.session_interprocess_lock(
                    sid, timeout=15.0,
                    root=self.root_path if self._explicit_root else None,
                ):
                    with self._lock:
                        self._sessions.pop(sid, None)
                    if project_id:
                        nested = shared.nested_session_dir(root, project_id, sid)
                        if shared.session_looks_present(nested):
                            self._record_location(sid, nested)
                            moved += 1
                            continue
                    new_legacy = legacy_project_session_dir(base, sid)
                    if shared.session_looks_present(new_legacy):
                        row = dict((journal.get("sessions") or {}).get(sid) or {})
                        row.update({
                            "session_id": sid,
                            "project_id": project_id or "",
                            "source": str(new_legacy),
                            "stage": "location-repair",
                            "old_path": old_path or "",
                        })
                        journal.setdefault("sessions", {})[sid] = row
                        update_journal_row(root, sid, row)
                        self._record_location(sid, new_legacy)
                        moved += 1
            moved += 0
        return moved


    def _forget_location(self, session_id: str) -> None:
        """删会话时移除位置映射（配对 _record_location）。"""
        with self._session_lock(session_id), shared.registry_file_lock(self.root_path, "locations"):
            snapshot = self._load_locations()
            if session_id not in snapshot:
                return
            snapshot.pop(session_id)
            self._save_locations(snapshot)
            with self._lock:
                self._locations.pop(session_id, None)


    def _index_path(self) -> shared.Path:
        return self.root_path / shared.INDEX_FILE


    def _load_index(self) -> None:
        p = self._index_path()
        if p.exists():
            try:
                data = shared.json.loads(shared.read_text_with_retry(p))
                if isinstance(data, dict):
                    self._index = data
                    self._reset_running_status()
                    self._startup_cleanup()
                    self._sweep_deleted_leftovers()
                    return
            except (OSError, shared.json.JSONDecodeError):
                shared._log.warning("index.json corrupted, rebuilding from session logs")
        self._rebuild_index()
        self._startup_cleanup()
        self._sweep_deleted_leftovers()


    def _rebuild_index(self) -> None:
        self._index = {}
        seen: set[str] = set()
        dirs_to_scan: list[shared.Path] = []
        for sid, sdir in shared.iter_session_dirs(self.root_path):
            if shared.is_deleted(self.root_path, sid):
                continue
            dirs_to_scan.append(sdir)
            seen.add(sid)
        for sid, loc in self._locations.items():
            if sid in seen or shared.is_deleted(self.root_path, sid):
                continue
            p = shared.Path(loc)
            if shared.session_looks_present(p):
                dirs_to_scan.append(p)
                seen.add(sid)
        for sdir in dirs_to_scan:
            try:
                log = shared.SessionLog(sdir)
                meta = log.read_meta()
                if not meta:
                    continue
                sid = meta.get("id") or sdir.name
                entry = self._meta_to_entry(meta)
                if not entry.get("created_at"):
                    try:
                        entry["created_at"] = log.main_path.stat().st_mtime
                    except OSError:
                        pass
                # Backfill preview from history.
                try:
                    text = self.latest_user_text(sid)
                    if text:
                        text = text.strip().replace("\n", " ")
                        entry["preview"] = (text[:77] + "…") if len(text) > 80 else text
                except Exception as e:  # noqa: BLE001 — preview is cosmetic
                    shared._log.debug("preview backfill failed for %s: %s", sid, e)
                self._index[sid] = entry
            except (OSError, shared.json.JSONDecodeError):
                continue
        self._reset_running_status()
        self._save_index()


    def _reset_running_status(self) -> None:
        for entry in self._index.values():
            if entry.get("status") == "running":
                entry["status"] = "idle"


    def _sweep_deleted_leftovers(self) -> None:
        """Remove what deleted conversations left outside their folders.

        A delete removes its lock files and releases its file history
        itself. This pass takes what a delete could not (a lock file another
        process had open, a delete that stopped half way) and the lock
        files made since by anything that locked an id with no
        conversation. Delete marks stay: they are what keeps a deleted id
        from coming back.

        Opening the store only reads the two lock folders' names. The
        removals run on a thread of their own (``_leftover_sweep``): the
        store may be opened on a server's event loop, and nothing waits
        for them. They touch no conversation: an idle lock file holds
        nothing, and a file history goes only when every conversation
        listed in it carries a delete mark of this store.
        """
        self._leftover_sweep = None
        try:
            with self._index_lock:
                known = set(self._index)
            stale = shared.stale_lock_files(self.root_path, known)
        except Exception:
            shared._log.warning("leftovers of deleted conversations not listed", exc_info=True)
            return
        # The shadow repositories live beside the default store only.
        histories = None
        if not self._explicit_root:
            from openprogram.state.store.shadow_git import store as shadow_git
            histories = shadow_git._shadow_root()
            if not histories.is_dir():
                histories = None
        if not stale and histories is None:
            return
        root = self.root_path

        def sweep() -> None:
            try:
                shared.sweep_lock_files(stale)
                if histories is not None:
                    shadow_git.sweep_released(lambda sid: shared.is_deleted(root, sid), histories)
            except Exception:
                shared._log.warning("leftovers of deleted conversations not swept", exc_info=True)

        self._leftover_sweep = shared.threading.Thread(
            target=sweep, name="openprogram-leftover-sweep", daemon=True)
        self._leftover_sweep.start()


    def _startup_cleanup(self) -> None:
        """Retire stale summary rows and log-less shells, losing nothing.

        Every session ``create_session`` writes has a log from its first
        meta write, so a directory without one is damaged or foreign, not
        an abandoned draft. Such a directory is kept in place when it
        holds any session data and otherwise moved to ``.quarantine``; this
        pass never deletes files. Anything it cannot judge with certainty
        (unknown age, unreadable migration journal, unresolved placement,
        a busy session lock, a filesystem error) is retained.
        """
        now = shared.time.time()
        try:
            from ..migration import load_journal
            journal_rows = load_journal(self.root_path, strict=True).get("sessions") or {}
        except Exception:
            shared._log.warning("migration journal unreadable; startup cleanup skipped",
                                exc_info=True)
            return
        present: set[str] | None = None
        dropped: list[str] = []
        for sid, entry in list(self._index.items()):
            if entry.get("archived") or shared.is_deleted(self.root_path, sid):
                continue
            created = entry.get("created_at")
            if (not isinstance(created, (int, float)) or isinstance(created, bool)
                    or now - created <= self._EMPTY_SHELL_AGE):
                continue
            row = journal_rows.get(sid)
            if row and row.get("stage") not in _MIGRATION_SETTLED:
                continue
            try:
                sdir = self._session_dir(sid)
                if (sdir / "main.jsonl").exists():
                    continue
                if not shared.os.path.lexists(sdir):
                    # A non-default placement that is absent is unreachable
                    # (moved project, offline volume), not gone.
                    if sdir != shared.default_session_dir(self.root_path, sid):
                        continue
                    if present is None:
                        present = {s for s, _ in shared.iter_session_dirs(self.root_path)}
                    if sid not in present:
                        dropped.append(sid)
                    continue
                if any(shared.os.path.lexists(sdir / name) for name in _SESSION_DATA):
                    shared._log.warning("session %s has no main log at %s; retained", sid, sdir)
                    continue
                if self._quarantine_shell(sid, sdir, now):
                    dropped.append(sid)
            except Exception:
                shared._log.warning("startup cleanup left %s untouched", sid, exc_info=True)
        if not dropped:
            return
        # Only the summary row goes. Project bindings and the location map
        # stay: they are harmless for a missing session and needed if a
        # quarantined directory is restored.
        with self._index_lock:
            for sid in dropped:
                if self._index.pop(sid, None) is not None:
                    self._index_generation += 1
        self._save_index()


    def _quarantine_shell(self, session_id: str, sdir: shared.Path, now: float) -> bool:
        """Move a log-less shell under ``.quarantine``. True when moved.

        Directories outside the store root (legacy working-folder copies)
        are never moved.
        """
        try:
            if not sdir.resolve().is_relative_to(self.root_path.resolve()):
                return False
        except OSError:
            return False
        dest = shared.quarantine_dir(self.root_path) / f"{session_id}-{int(now)}"
        try:
            with shared.session_interprocess_lock(
                session_id, blocking=False,
                root=self.root_path if self._explicit_root else None,
            ):
                if any(shared.os.path.lexists(sdir / name) for name in _SESSION_DATA):
                    return False
                dest.mkdir(parents=True)
                try:
                    shared.move_session_folder(sdir, dest / "session")
                except OSError:
                    dest.rmdir()
                    raise
                shared.atomic_write_text(dest / "reason.json", shared.json.dumps({
                    "session_id": session_id,
                    "source": str(sdir),
                    "reason": "no session data",
                    "quarantined_at": now,
                }, indent=2, ensure_ascii=False))
        except (BlockingIOError, OSError) as e:
            shared._log.warning("shell %s not quarantined: %s", sdir, e)
            return False
        shared._log.info("quarantined log-less shell %s -> %s", sdir, dest)
        return True


    def _meta_to_entry(self, meta: dict) -> dict:
        entry = {}
        for k in self._INDEX_FIELDS:
            if k in meta:
                entry[k] = meta[k]
        entry.setdefault("id", meta.get("id", ""))
        entry.setdefault("status", "idle")
        entry.setdefault("pinned", False)
        entry.setdefault("archived", False)
        entry.setdefault("unread", False)
        return entry


    def _save_index(self) -> None:
        with self._index_write_lock:
            with self._index_lock:
                snapshot = {sid: dict(entry) for sid, entry in self._index.items()}
                generation = self._index_generation
            try:
                shared.atomic_write_text(
                    self._index_path(),
                    shared.json.dumps(snapshot, indent=2, ensure_ascii=False,
                               default=str),
                )
            except OSError as e:
                shared._log.warning("index.json NOT saved (%s); session list may be "
                             "stale until the next rebuild", e)
                with self._index_lock:
                    self._index_dirty = True
                return
            with self._index_lock:
                if self._index_generation == generation:
                    self._index_dirty = False


    def _schedule_index_flush(self) -> None:
        with self._index_lock:
            self._index_dirty = True
            if self._index_background_enabled:
                if self._index_timer is not None:
                    return
                self._index_flush_threads = {
                    thread for thread in self._index_flush_threads if thread.is_alive()
                }
                self._index_timer = shared.threading.Timer(5.0, self._do_deferred_flush)
                self._index_timer.daemon = True
                self._index_flush_threads.add(self._index_timer)
                try:
                    self._index_timer.start()
                except RuntimeError as exc:
                    # Thread exhaustion must not strand dirty state or leave
                    # an unstarted handle that close() cannot join.
                    self._index_flush_threads.discard(self._index_timer)
                    self._index_timer = None
                    shared._log.warning("index timer unavailable; flushing synchronously: %s", exc)
                else:
                    return
        # close() stops background work. Legacy callers can still reuse the
        # filesystem store, but subsequent registry writes are synchronous.
        self._save_index()


    def _do_deferred_flush(self) -> None:
        with self._index_lock:
            if self._index_timer is shared.threading.current_thread():
                self._index_timer = None
            dirty = self._index_dirty
        if dirty:
            self._save_index()


    def _flush_index(self) -> None:
        with self._index_lock:
            threads = tuple(self._index_flush_threads)
            for thread in threads:
                thread.cancel()
            self._index_timer = None
        # Timer.cancel() only prevents callbacks that have not started. Keep
        # handles for in-flight callbacks and join outside both index locks.
        for thread in threads:
            if thread is not shared.threading.current_thread():
                thread.join()
        with self._index_lock:
            self._index_flush_threads.difference_update(
                thread for thread in threads if not thread.is_alive()
            )
            dirty = self._index_dirty
        if dirty:
            self._save_index()


    def _update_index_entry(self, session_id: str, **fields: shared.Any) -> None:
        with self._index_lock:
            entry = self._index.get(session_id)
            if entry is None:
                entry = {"id": session_id, "status": "idle", "pinned": False,
                         "archived": False, "unread": False}
                self._index[session_id] = entry
            # updated_at 只由调用方显式传入（追加消息的路径）——改名/置顶/
            # 标已读不算"最新一次聊天"，不许把会话顶到侧栏最上。
            for k, v in fields.items():
                if k in self._INDEX_FIELDS or k == "preview":
                    entry[k] = v
            self._index_generation += 1
            self._index_dirty = True

