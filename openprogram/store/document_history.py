"""Project-owned document history; file transactions belong to CheckpointStore."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import time
import uuid
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
                """)
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

    def list(self, project_id: str, relative: str, *, limit: int = 50, cursor: int = 0) -> dict:
        _owner(project_id)
        relative = _relative(relative)
        limit, cursor = max(1, min(int(limit), 100)), max(0, int(cursor))
        with self._database() as db:
            rows = db.execute("SELECT * FROM groups WHERE project_id=? AND path=? ORDER BY sequence DESC LIMIT ? OFFSET ?",
                              (project_id, relative, limit + 1, cursor)).fetchall()
            return {"entries": [self._entry(row) for row in rows[:limit]],
                    "next_cursor": str(cursor + limit) if len(rows) > limit else None}

    def content(self, project_id: str, relative: str, version_id: str, side: str = "after") -> bytes:
        _owner(project_id)
        relative = _relative(relative)
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

    def restore(self, project_id: str, relative: str, version_id: str, *, side: str,
                baseline_revision: str, idempotency_key: str, editor_id: str = "manual") -> dict:
        raw = self.content(project_id, relative, version_id, side)
        return self.publish(project_id, relative, raw, baseline_revision=baseline_revision,
                            idempotency_key=idempotency_key, editor_id=editor_id, close=True,
                            restored_from={"version_id": version_id, "side": side})
