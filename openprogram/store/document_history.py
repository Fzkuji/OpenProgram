"""Project-owned document history; file transactions belong to CheckpointStore."""
from __future__ import annotations

import hashlib
import base64
import json
import os
import re
import sqlite3
import stat
import tempfile
import time
import uuid
import math
from contextlib import contextmanager
from pathlib import Path

from openprogram.store.session.session_lock import registry_file_lock
from openprogram.store.snapshot.checkpoint.store import CheckpointStore

MAX_BYTES = 64 * 1024 * 1024
GROUP_SECONDS = 300.0


class DocumentHistoryError(RuntimeError):
    def __init__(self, message: str, code: str = "DOCUMENT_HISTORY_ERROR"):
        super().__init__(message)
        self.code = code


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _owner(project_id: str):
    from openprogram.store.project import project_store
    if not isinstance(project_id, str) or not project_id:
        raise DocumentHistoryError("project_id is required", "INVALID_REQUEST")
    project = project_store.get_project(project_id)
    if project is None:
        raise DocumentHistoryError("unknown project", "NOT_FOUND")
    return project


def _relative(path: str) -> str:
    if not isinstance(path, str) or not path or "\x00" in path or "\\" in path:
        raise DocumentHistoryError("path must be a project-relative file", "INVALID_REQUEST")
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.name:
        raise DocumentHistoryError("path escapes project root", "INVALID_REQUEST")
    return candidate.as_posix()


def resolve_document(project_id: str, relative: str) -> tuple[Path, str]:
    relative = _relative(relative)
    project = _owner(project_id)
    from openprogram.store.project.location import bound_execution_state, refresh_project_location
    if not getattr(project, "is_default", False):
        refresh_project_location(project_id)
        project = _owner(project_id)
        if bound_execution_state(project) is not None:
            raise DocumentHistoryError("project location unavailable", "PROJECT_LOCATION_UNAVAILABLE")
    root = Path(project.path).expanduser().resolve()
    if not root.is_dir():
        raise DocumentHistoryError("project location unavailable", "PROJECT_LOCATION_UNAVAILABLE")
    target = root
    for part in Path(relative).parts:
        target = target / part
        if target.is_symlink():
            raise DocumentHistoryError("linked document paths are not writable", "INVALID_REQUEST")
    if not target.resolve().is_relative_to(root):
        raise DocumentHistoryError("path escapes project root", "INVALID_REQUEST")
    if not target.parent.is_dir():
        raise DocumentHistoryError("parent directory does not exist", "NOT_FOUND")
    return target, relative


def _revision(state: dict | None) -> str | None:
    if not isinstance(state, dict):
        return None
    if state.get("kind") == "absent":
        return "absent"
    value = state.get("digest") or state.get("sha256")
    return value.removeprefix("sha256:") if isinstance(value, str) else None


class DocumentHistory:
    """A durable, paginated metadata index referring to shared transaction blobs."""

    def __init__(self, root: Path | None = None):
        from openprogram.paths import get_state_dir
        self.root = Path(root) if root is not None else get_state_dir() / "project-file-history"

    def _dir(self, project_id: str, relative: str) -> Path:
        return self.root / hashlib.sha256(project_id.encode()).hexdigest() / hashlib.sha256(relative.encode()).hexdigest()

    def _store(self, project_id: str, relative: str) -> CheckpointStore:
        return CheckpointStore(recovery_root=self._dir(project_id, relative))

    @contextmanager
    def _database(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with registry_file_lock(self.root, "documents"):
            db = sqlite3.connect(self.root / "history.sqlite3")
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA synchronous=FULL")
                schema = db.execute("PRAGMA user_version").fetchone()[0]
                if schema not in {0, 1}:
                    raise DocumentHistoryError("unsupported document history schema", "HISTORY_CORRUPT")
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS operations (
                        operation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                        path TEXT NOT NULL, request_key TEXT NOT NULL,
                        fingerprint TEXT NOT NULL, group_id TEXT NOT NULL,
                        result_json TEXT, UNIQUE(project_id, path, request_key));
                    CREATE TABLE IF NOT EXISTS groups (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT, version_id TEXT UNIQUE NOT NULL,
                        project_id TEXT NOT NULL, path TEXT NOT NULL, editor_id TEXT NOT NULL,
                        started REAL NOT NULL, updated REAL NOT NULL, closed INTEGER NOT NULL,
                        first_op TEXT NOT NULL, last_op TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS group_path ON groups(project_id,path,sequence DESC);
                    CREATE TABLE IF NOT EXISTS model_versions (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        version_id TEXT UNIQUE NOT NULL, project_id TEXT NOT NULL,
                        path TEXT NOT NULL, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                        original_path TEXT NOT NULL, started REAL NOT NULL, entry_json TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS model_version_path ON model_versions(project_id,path,started);
                    CREATE TABLE IF NOT EXISTS model_backfills (
                        project_id TEXT PRIMARY KEY, queue_json TEXT NOT NULL,
                        status TEXT NOT NULL, issues INTEGER NOT NULL DEFAULT 0);
                """)
                if schema == 0:
                    db.execute("PRAGMA user_version=1")
                    db.commit()
                os.chmod(self.root / "history.sqlite3", 0o600)
                yield db
            except sqlite3.Error as exc:
                raise DocumentHistoryError("document history index is unavailable", "HISTORY_CORRUPT") from exc
            finally:
                db.close()

    @staticmethod
    def _read_bounded(path: Path) -> tuple[bytes, int]:
        try:
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise DocumentHistoryError("target must be an ordinary file", "INVALID_REQUEST")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            with os.fdopen(os.open(path, flags), "rb") as handle:
                opened = os.fstat(handle.fileno())
                if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                    raise DocumentHistoryError("document changed while opening", "CONFLICT")
                raw = handle.read(MAX_BYTES + 1)
                after = os.fstat(handle.fileno())
            if len(raw) > MAX_BYTES:
                raise DocumentHistoryError("content exceeds 64 MiB", "PAYLOAD_TOO_LARGE")
            identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            if identity(opened) != identity(after) or identity(after) != identity(os.lstat(path)):
                raise DocumentHistoryError("document changed while reading", "CONFLICT")
            return raw, stat.S_IMODE(after.st_mode)
        except FileNotFoundError as exc:
            raise DocumentHistoryError("document not found", "NOT_FOUND") from exc

    def _receipt(self, project_id: str, relative: str, operation_id: str) -> dict:
        try:
            receipt = self._store(project_id, relative).read_document_operation(operation_id)
        except (ValueError, OSError) as exc:
            raise DocumentHistoryError("document transaction is corrupt", "HISTORY_CORRUPT") from exc
        if not receipt or receipt.get("status") == "not_found":
            return {"status": "recovery_required", "error_code": "RECOVERY_REQUIRED"}
        return receipt

    def _entry(self, row) -> dict:
        first = self._receipt(row["project_id"], row["path"], row["first_op"])
        last = self._receipt(row["project_id"], row["path"], row["last_op"])
        return {"version_id": row["version_id"], "project_id": row["project_id"], "path": row["path"],
                "editor_id": row["editor_id"], "actor": "user", "group_started_at": row["started"],
                "created_at": row["updated"], "status": last.get("status", "recovery_required"),
                "before_revision": _revision(first.get("before")),
                "after_revision": _revision(last.get("after")) if last.get("status") == "committed" else None}

    def publish(self, project_id: str, relative: str, content: bytes, *, editor_id: str = "manual",
                baseline_revision: str | None = None, idempotency_key: str | None = None,
                expected_mtime: float | None = None, close: bool = False,
                restored_from: dict | None = None) -> dict:
        _owner(project_id)
        relative = _relative(relative)
        if not isinstance(content, bytes) or len(content) > MAX_BYTES:
            raise DocumentHistoryError("content exceeds 64 MiB", "PAYLOAD_TOO_LARGE")
        if not isinstance(editor_id, str) or not editor_id or len(editor_id) > 128:
            raise DocumentHistoryError("invalid editor id", "INVALID_REQUEST")
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 128):
            raise DocumentHistoryError("invalid idempotency key", "INVALID_REQUEST")
        key = idempotency_key or str(uuid.uuid4())
        fingerprint = _digest(json.dumps({"revision": baseline_revision, "mtime": expected_mtime,
                            "digest": _digest(content), "editor": editor_id, "close": close, "restored_from": restored_from},
                            sort_keys=True).encode())
        with self._database() as db:
            existing = db.execute("SELECT * FROM operations WHERE project_id=? AND path=? AND request_key=?",
                                  (project_id, relative, key)).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise DocumentHistoryError("idempotency key payload conflict", "CONFLICT")
                if existing["result_json"]:
                    return json.loads(existing["result_json"])
                return self._operation_result(db, existing)
            target, relative = resolve_document(project_id, relative)
            # A fresh read is used only for grouping. CheckpointStore independently
            # checks the expected revision and captures the actual immutable before.
            try:
                current, _ = self._read_bounded(target)
                current_revision = _digest(current)
            except DocumentHistoryError as exc:
                if exc.code != "NOT_FOUND":
                    raise
                current_revision = "absent"
            if baseline_revision is not None and baseline_revision != current_revision:
                raise DocumentHistoryError("baseline revision does not match", "CONFLICT")
            if expected_mtime is not None and (not target.exists() or target.stat().st_mtime != expected_mtime):
                raise DocumentHistoryError("document changed on disk", "CONFLICT")
            previous = db.execute("SELECT * FROM groups WHERE project_id=? AND path=? ORDER BY sequence DESC LIMIT 1",
                                  (project_id, relative)).fetchone()
            now = time.time()
            same_group = False
            if restored_from is None and previous and not previous["closed"] and previous["editor_id"] == editor_id and 0 <= now - previous["started"] < GROUP_SECONDS:
                last = self._receipt(project_id, relative, previous["last_op"])
                same_group = last.get("status") == "committed" and _revision(last.get("after")) == current_revision
            operation_id = uuid.uuid4().hex
            group_id = uuid.uuid4().hex
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,NULL)",
                       (operation_id, project_id, relative, key, fingerprint, group_id))
            # Keep a pending publication separate until committed. An interrupted
            # autosave must not hide the previous confirmed group's after version.
            db.execute("INSERT INTO groups(version_id,project_id,path,editor_id,started,updated,closed,first_op,last_op) VALUES(?,?,?,?,?,?,?,?,?)",
                       (group_id, project_id, relative, editor_id, now, now, int(close), operation_id, operation_id))
            db.commit()  # Intent locator is durable before any target mutation.
            with tempfile.TemporaryDirectory(prefix="document-", dir=self.root) as staging:
                source = Path(staging) / "content"
                with source.open("xb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._store(project_id, relative).publish_document(
                    operation_id, str(target), str(source), expected_revision=current_revision,
                    expected_mtime=expected_mtime, fingerprint=fingerprint,
                    metadata={"project_id": project_id, "path": relative, "actor": "user", "restored_from": restored_from})
            receipt = self._receipt(project_id, relative, operation_id)
            if same_group and receipt.get("status") == "committed":
                db.execute("UPDATE groups SET last_op=?, updated=?, closed=? WHERE version_id=?",
                           (operation_id, now, int(close), previous["version_id"]))
                db.execute("UPDATE operations SET group_id=? WHERE operation_id=?",
                           (previous["version_id"], operation_id))
                db.execute("DELETE FROM groups WHERE version_id=?", (group_id,))
                db.commit()
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            return self._operation_result(db, row)

    def _operation_result(self, db, operation) -> dict:
        receipt = self._receipt(operation["project_id"], operation["path"], operation["operation_id"])
        status = receipt.get("status", "recovery_required")
        result = {"version_id": operation["group_id"], "operation_id": operation["operation_id"],
                  "status": status, "ok": status == "committed", "revision": _revision(receipt.get("after")),
                  "mtime": receipt.get("mtime")}
        if receipt.get("error_code"):
            result["error_code"] = receipt["error_code"]
        if receipt.get("error"):
            result["error"] = receipt["error"]
        if status != "committed":
            result["error_code"] = result.get("error_code") or ("CONFLICT" if status in {"blocked", "aborted"} else "RECOVERY_REQUIRED")
            result["error"] = result.get("error") or "document publication did not complete"
        if status in {"committed", "aborted", "rolled_back", "recovery_required"}:
            db.execute("UPDATE operations SET result_json=? WHERE operation_id=?", (json.dumps(result), operation["operation_id"]))
            db.commit()
        return result

    @staticmethod
    def _session_store():
        from openprogram.store import default_store
        return default_store()

    @staticmethod
    def _model_session(session_id, store):
        from openprogram.store.session.placement import is_deleted
        if (not isinstance(session_id, str) or not session_id or
                session_id in {".", ".."} or "/" in session_id or "\\" in session_id):
            return None
        if is_deleted(store.root_path, session_id):
            return None
        return store.get_session(session_id)

    def register_model_turn(self, session_id: str, turn_id: str, *, session_store=None, legacy_project_id=None) -> None:
        """Index locators only; immutable checkpoint manifests retain byte authority.

        The trusted helper can call this while holding its session lock. Never
        acquire a session lock while holding the history database lock.
        """
        store = session_store or self._session_store()
        session = self._model_session(session_id, store)
        if not session:
            return
        if not isinstance(turn_id, str) or not turn_id or "\\" in turn_id or Path(turn_id).name != turn_id or turn_id in {".", ".."}:
            raise DocumentHistoryError("invalid model turn", "INVALID_REQUEST")
        rows = CheckpointStore(store._session_dir(session_id)).list_file_history(turn_id)
        values = []
        for entry in rows:
            locator = entry.get("project_locator")
            if not isinstance(locator, dict) and legacy_project_id:
                try:
                    project = _owner(legacy_project_id)
                    recorded_root = session.get("project_path")
                    if session.get("project_id") != project.id or recorded_root != project.path:
                        continue
                    relative = Path(entry["path"]).relative_to(Path(recorded_root)).as_posix()
                    target, relative = resolve_document(project.id, relative)
                    if str(target) != entry["path"]:
                        continue
                    locator = {"project_id": project.id, "path": relative, "recorded_root": recorded_root,
                               "directory_identity": getattr(project, "directory_identity", ""),
                               "location_revision": getattr(project, "location_revision", 0)}
                    entry = {**entry, "project_locator": locator, "legacy_locator_verified": True}
                except (DocumentHistoryError, TypeError, ValueError):
                    continue
            if not isinstance(locator, dict):
                continue
            project_id = locator.get("project_id")
            try:
                _owner(project_id)
                relative = _relative(locator.get("path"))
                started = float(entry.get("prepared_at") or entry.get("committed_at") or 0)
                if not math.isfinite(started):
                    continue
            except (DocumentHistoryError, TypeError, ValueError):
                continue
            version = "m-" + uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
                [project_id, relative, session_id, turn_id, entry["path"]])).hex
            values.append((version, project_id, relative, session_id, turn_id,
                           entry["path"], started, json.dumps(entry)))
        if values:
            with self._database() as db:
                db.executemany("""INSERT INTO model_versions
                    (version_id,project_id,path,session_id,turn_id,original_path,started,entry_json)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(version_id) DO UPDATE SET entry_json=excluded.entry_json""", values)
                db.commit()

    def backfill_project(self, project_id: str, *, limit: int = 20) -> dict:
        """Resume a bounded manifest batch over a captured project registry list."""
        _owner(project_id)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with registry_file_lock(self.root, "model-backfill-" + hashlib.sha256(project_id.encode()).hexdigest()):
            return self._backfill_project_locked(project_id, limit=limit)

    def _backfill_project_locked(self, project_id: str, *, limit: int) -> dict:
        project = _owner(project_id)
        store = self._session_store()
        with self._database() as db:
            saved = db.execute("SELECT * FROM model_backfills WHERE project_id=?", (project_id,)).fetchone()
        if saved and saved["status"] in {"complete", "unavailable"}:
            return {"state": saved["status"], "unavailable_count": saved["issues"]}
        if saved:
            try:
                queue = json.loads(saved["queue_json"])
                if not isinstance(queue, list) or len(queue) > 100_000 or any(
                    not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str)
                    or (item[1] is not None and (not isinstance(item[1], list)
                        or any(not isinstance(turn, str) or Path(turn).name != turn or "\\" in turn
                               or turn in {".", ".."} for turn in item[1]))) for item in queue
                ):
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise DocumentHistoryError("model history migration state is corrupt", "HISTORY_CORRUPT") from exc
            issues = saved["issues"]
        else:
            # Only registry metadata is enumerated here, once. Session contents
            # and turn manifests are opened within the bounded batch below.
            sessions = sorted(set(getattr(project, "session_ids", []) or []))
            queue = [[sid, None] for sid in sessions[:100_000]]
            issues = int(len(sessions) > 100_000)
            with self._database() as db:
                db.execute("INSERT OR IGNORE INTO model_backfills VALUES(?,?,?,?)",
                           (project_id, json.dumps(queue), "partial", issues))
                db.commit()
        remaining = max(1, min(int(limit), 100))
        from openprogram.store.snapshot.checkpoint.paths import session_backup_root, turn_manifest_path
        from openprogram.store.snapshot.checkpoint import manifest
        while queue and remaining:
            sid, turns = queue[0]
            if not self._model_session(sid, store):
                from openprogram.store.session.placement import is_deleted
                if not is_deleted(store.root_path, sid):
                    issues += 1
                queue.pop(0)
                remaining -= 1
                continue
            session_dir = store._session_dir(sid)
            if turns is None:
                roots = (session_backup_root(session_dir), Path(session_dir) / "file_backups")
                turns = sorted({path.name for root in roots if root.is_dir()
                                for path in root.iterdir() if path.is_dir() and (path / "manifest.json").is_file()})
                queue[0][1] = turns
            if not turns:
                queue.pop(0)
                remaining -= 1
                continue
            turn = turns.pop(0)
            remaining -= 1
            try:
                entries = manifest.load(turn_manifest_path(session_dir, turn)).get("files", {})
                # A legacy absolute path alone cannot establish ownership after
                # relocation. Leave it in session Review and report the gap.
                self.register_model_turn(sid, turn, session_store=store, legacy_project_id=project_id)
                with self._database() as db:
                    mapped = {row[0] for row in db.execute("SELECT original_path FROM model_versions WHERE project_id=? AND session_id=? AND turn_id=?",
                                                         (project_id, sid, turn))}
                issues += sum(entry["path"] not in mapped for entry in entries.values())
            except (OSError, ValueError, DocumentHistoryError):
                issues += 1
        status = "partial" if queue else ("unavailable" if issues else "complete")
        with self._database() as db:
            db.execute("UPDATE model_backfills SET queue_json=?, status=?, issues=? WHERE project_id=?",
                       (json.dumps(queue), status, issues, project.id))
            db.commit()
        return {"state": status, "unavailable_count": issues}

    def _model_entry(self, row) -> dict | None:
        store = self._session_store()
        if not self._model_session(row["session_id"], store):
            return None
        session_dir = store._session_dir(row["session_id"])
        try:
            receipts = CheckpointStore(session_dir).list_file_history(row["turn_id"])
            entry = next((value for value in receipts if value["path"] == row["original_path"]), {})
        except (OSError, ValueError):
            entry = {}
        from openprogram.store.snapshot.checkpoint.paths import turn_backup_dir
        def retained_revision(side):
            state = entry.get(side)
            if not isinstance(state, dict):
                return None
            if state.get("kind") == "absent":
                return "absent"
            ref = state.get("blob_ref")
            if not isinstance(ref, str) or not ref or Path(ref).name != ref:
                return None
            blob = turn_backup_dir(session_dir, row["turn_id"]) / ref
            return _revision(state) if blob.is_file() and not blob.is_symlink() else None
        confirmed = entry.get("status") == "committed" and not entry.get("pending")
        after = retained_revision("after") if confirmed else None
        return {"version_id": row["version_id"], "project_id": row["project_id"], "path": row["path"],
                "session_id": row["session_id"], "turn_id": row["turn_id"], "actor": "model",
                "created_at": entry.get("committed_at") or row["started"], "group_started_at": row["started"],
                "status": "committed" if confirmed and after else "recovery_required",
                "before_revision": retained_revision("before"), "after_revision": after}

    def list(self, project_id: str, relative: str, *, limit: int = 50, cursor: str | int = 0) -> dict:
        _owner(project_id)
        relative = _relative(relative)
        limit = max(1, min(int(limit), 100))
        index_state = self.backfill_project(project_id)
        offset = 0
        page = None
        if cursor and str(cursor).isdigit():
            offset = max(0, int(cursor))  # Compatibility with earlier manual pages.
        elif cursor:
            try:
                if len(str(cursor)) > 1024:
                    raise ValueError
                page = json.loads(base64.urlsafe_b64decode(str(cursor)).decode())
                if (page["project"] != project_id or page["path"] != relative
                        or not math.isfinite(page["time"]) or not isinstance(page["id"], str)
                        or not all(isinstance(page[key], int) and page[key] >= 0 for key in ("manual", "model"))):
                    raise ValueError
            except (ValueError, TypeError, KeyError):
                raise DocumentHistoryError("invalid history cursor", "INVALID_REQUEST") from None
        with self._database() as db:
            caps = page or {"manual": db.execute("SELECT COALESCE(MAX(sequence),0) FROM groups").fetchone()[0],
                            "model": db.execute("SELECT COALESCE(MAX(sequence),0) FROM model_versions").fetchone()[0]}
        # Validate source rows outside the SQLite lock. Deleted records cannot
        # hide an older visible row; work remains bounded even after mass deletion.
        visible, last, exhausted = [], page, False
        scanned = 0
        while len(visible) <= limit and scanned < (limit + 1) * 4:
            time_key = last["time"] if last else float("inf")
            id_key = last["id"] if last else "~"
            with self._database() as db:
                rows = db.execute("""SELECT version_id,started,kind FROM (
                    SELECT version_id,started,'manual' kind FROM groups WHERE project_id=? AND path=? AND sequence<=?
                    UNION ALL SELECT version_id,started,'model' kind FROM model_versions WHERE project_id=? AND path=? AND sequence<=?
                    ) WHERE started < ? OR (started=? AND version_id<?)
                    ORDER BY started DESC,version_id DESC LIMIT ? OFFSET ?""",
                    (project_id, relative, caps["manual"], project_id, relative, caps["model"],
                     time_key, time_key, id_key, limit + 1, offset)).fetchall()
                details = []
                for row in rows:
                    table = "groups" if row["kind"] == "manual" else "model_versions"
                    detail = dict(db.execute(f"SELECT * FROM {table} WHERE version_id=?", (row["version_id"],)).fetchone())
                    details.append((row["kind"], detail))
            offset = 0
            if not rows:
                exhausted = True
                break
            for kind, row in details:
                scanned += 1
                last = {"time": row["started"], "id": row["version_id"]}
                value = self._entry(row) if kind == "manual" else self._model_entry(row)
                if value:
                    visible.append((value, dict(last)))
                    if len(visible) > limit:
                        break
            if len(rows) < limit + 1 and len(visible) <= limit:
                exhausted = True
                break
        next_cursor = None
        if not exhausted and last:
            boundary = visible[limit - 1][1] if len(visible) > limit else last
            payload = {**caps, "project": project_id, "path": relative, **boundary}
            next_cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        return {"entries": [value for value, _ in visible[:limit]],
                "next_cursor": next_cursor, "model_index": index_state}

    def content(self, project_id: str, relative: str, version_id: str, side: str = "after") -> bytes:
        _owner(project_id)
        relative = _relative(relative)
        if isinstance(version_id, str) and version_id.startswith("m-"):
            return self._model_content(project_id, relative, version_id, side)
        if side not in {"before", "after"} or not isinstance(version_id, str) or not re.fullmatch(r"[a-f0-9]{32}", version_id):
            raise DocumentHistoryError("invalid history version or side", "INVALID_REQUEST")
        with self._database() as db:
            group = db.execute("SELECT * FROM groups WHERE project_id=? AND path=? AND version_id=?",
                               (project_id, relative, version_id)).fetchone()
            if group is None:
                raise DocumentHistoryError("version not found", "NOT_FOUND")
            operation = group["first_op"] if side == "before" else group["last_op"]
            receipt = self._receipt(project_id, relative, operation)
            state = receipt.get(side)
            if not isinstance(state, dict) or (side == "after" and receipt.get("status") != "committed"):
                raise DocumentHistoryError("version is not confirmed", "RECOVERY_REQUIRED")
            if state.get("kind") == "absent":
                raise DocumentHistoryError("file did not exist in this version", "NOT_FOUND")
            ref = state.get("blob_ref")
            if not isinstance(ref, str) or not ref or Path(ref).name != ref or ref in {".", ".."}:
                raise DocumentHistoryError("invalid version reference", "HISTORY_CORRUPT")
            raw, _ = self._read_bounded(self._dir(project_id, relative) / "operations" / operation / ref)
            if _digest(raw) != _revision(state):
                raise DocumentHistoryError("version content is corrupt", "HISTORY_CORRUPT")
            return raw

    def _model_content(self, project_id: str, relative: str, version_id: str, side: str) -> bytes:
        if side not in {"before", "after"} or not re.fullmatch(r"m-[a-f0-9]{32}", version_id):
            raise DocumentHistoryError("invalid history version or side", "INVALID_REQUEST")
        with self._database() as db:
            row = db.execute("SELECT * FROM model_versions WHERE project_id=? AND path=? AND version_id=?",
                             (project_id, relative, version_id)).fetchone()
        if row is None:
            raise DocumentHistoryError("model version not found", "NOT_FOUND")
        store = self._session_store()
        from openprogram.store.session.session_lock import session_interprocess_lock
        from openprogram.store.snapshot.checkpoint.paths import turn_backup_dir
        with session_interprocess_lock(row["session_id"], root=store.root_path if store._explicit_root else None):
            if not self._model_session(row["session_id"], store):
                raise DocumentHistoryError("original conversation was deleted", "NOT_FOUND")
            session_dir = store._session_dir(row["session_id"])
            try:
                entries = CheckpointStore(session_dir).list_file_history(row["turn_id"])
            except (OSError, ValueError) as exc:
                raise DocumentHistoryError("model history is corrupt", "HISTORY_CORRUPT") from exc
            entry = next((value for value in entries if value["path"] == row["original_path"]), None)
            if entry is None:
                raise DocumentHistoryError("model version is no longer retained", "NOT_FOUND")
            locator = entry.get("project_locator")
            if not locator:
                try:
                    indexed = json.loads(row["entry_json"])
                    locator = indexed.get("project_locator") if indexed.get("legacy_locator_verified") else {}
                except (ValueError, TypeError, AttributeError) as exc:
                    raise DocumentHistoryError("model history index is corrupt", "HISTORY_CORRUPT") from exc
            if locator.get("project_id") != project_id or locator.get("path") != relative:
                raise DocumentHistoryError("model version ownership mismatch", "HISTORY_CORRUPT")
            state = entry.get(side)
            if (not isinstance(state, dict) or (side == "after" and
                    (entry.get("status") != "committed" or entry.get("pending")))):
                raise DocumentHistoryError("model version is not confirmed", "RECOVERY_REQUIRED")
            if state.get("kind") == "absent":
                raise DocumentHistoryError("file did not exist in this version", "NOT_FOUND")
            ref = state.get("blob_ref")
            if (state.get("kind") != "regular" or not isinstance(ref, str) or not ref
                    or Path(ref).name != ref or ref in {".", ".."}):
                raise DocumentHistoryError("model version is unavailable", "RECOVERY_REQUIRED")
            raw, _ = self._read_bounded(turn_backup_dir(session_dir, row["turn_id"]) / ref)
            if _digest(raw) != _revision(state):
                raise DocumentHistoryError("model version content is corrupt", "HISTORY_CORRUPT")
            return raw

    def restore(self, project_id: str, relative: str, version_id: str, *, side: str,
                baseline_revision: str, idempotency_key: str, editor_id: str = "manual") -> dict:
        raw = self.content(project_id, relative, version_id, side)
        return self.publish(project_id, relative, raw, baseline_revision=baseline_revision,
                            idempotency_key=idempotency_key, editor_id=editor_id, close=True,
                            restored_from={"version_id": version_id, "side": side})
