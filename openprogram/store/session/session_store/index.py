"""SessionStore index operations."""
from __future__ import annotations
from . import shared


class IndexOperations:
    def _locations_path(self) -> shared.Path:
        return self.root_path / "locations.json"


    def _load_locations(self) -> dict[str, str]:
        p = self.root_path / "locations.json"
        if not p.exists():
            return {}
        try:
            data = shared.json.loads(shared.read_text_with_retry(p))
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
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
        return self.root_path / "index.json"


    def _load_index(self) -> None:
        p = self._index_path()
        if p.exists():
            try:
                data = shared.json.loads(shared.read_text_with_retry(p))
                if isinstance(data, dict):
                    self._index = data
                    self._reset_running_status()
                    self._startup_cleanup()
                    return
            except (OSError, shared.json.JSONDecodeError):
                shared._log.warning("index.json corrupted, rebuilding from meta.json files")
        self._rebuild_index()
        self._startup_cleanup()


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
                meta = shared.json.loads(shared.read_text_with_retry(sdir / "meta.json"))
                sid = meta.get("id") or sdir.name
                entry = self._meta_to_entry(meta)
                if not entry.get("created_at"):
                    try:
                        entry["created_at"] = (sdir / "meta.json").stat().st_mtime
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


    def _startup_cleanup(self) -> None:
        now = shared.time.time()
        to_delete: list[str] = []
        for sid, entry in list(self._index.items()):
            created = entry.get("created_at") or 0
            sdir = self._session_dir(sid)
            if shared.is_deleted(self.root_path, sid):
                continue
            # Unreachable or still-migrating sessions are not empty shells.
            if sdir != self.root_path / sid and not sdir.exists():
                continue
            try:
                from ..migration import load_journal
                row = (load_journal(self.root_path).get("sessions") or {}).get(sid)
                if row and row.get("stage") in {"pending", "deferred", "failed", "inventory", "copy", "verify", "publish"}:
                    continue
            except Exception:
                shared._log.debug("failed to inspect migration journal for %s", sid,
                           exc_info=True)
            # Explicit archives remain available until the owner deletes them.
            if entry.get("archived"):
                continue
            # Empty shells: no history, older than 1 hour.
            if (now - created) > self._EMPTY_SHELL_AGE:
                # An existing history directory is sufficient evidence to
                # retain the session. Listing it here makes worker startup
                # depend on remote filesystem directory enumeration (for
                # example project-bound CloudStorage sessions). Empty
                # history directories are harmless; missing/non-directory
                # history paths remain eligible for cleanup.
                if not (sdir / "history").is_dir():
                    to_delete.append(sid)
                    continue
        for sid in to_delete:
            self._index.pop(sid, None)
            shared.remove_tree(self._session_dir(sid), ignore_errors=True)
            self._forget_stale_bindings(sid)
        dirty = bool(to_delete)
        if dirty:
            self._save_index()


    def _forget_stale_bindings(self, session_id: str) -> None:
        """Same location + project cleanup delete_session does — startup
        cleanup removed the directory but left the locations map and the
        project reverse index pointing at it, so both grew stale entries
        forever."""
        self._forget_location(session_id)
        try:
            from openprogram.store.project import project_store as _projects
            _projects.unbind_session(session_id)
        except Exception as e:  # noqa: BLE001 — reverse index is best-effort
            shared._log.warning("session %s NOT unbound from its project: %s",
                         session_id, e)


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

