"""Durable, project-owned history for manual document publications."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import time
import uuid
from pathlib import Path

MAX_BYTES = 64 * 1024 * 1024
GROUP_SECONDS = 300.0


class DocumentHistoryError(RuntimeError):
    def __init__(self, message: str, code: str = "DOCUMENT_HISTORY_ERROR"):
        super().__init__(message)
        self.code = code


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _project_root(project_id: str) -> Path:
    from openprogram.store.project import project_store
    project = project_store.get_project(project_id)
    if project is None:
        raise DocumentHistoryError("unknown project", "NOT_FOUND")
    if getattr(project, "location_state", "available") not in {"available", ""}:
        raise DocumentHistoryError("project location unavailable", "NOT_FOUND")
    root = Path(project.path).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise DocumentHistoryError("project location unavailable", "NOT_FOUND")
    return root.resolve()


def resolve_document(project_id: str, relative: str) -> tuple[Path, str]:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise DocumentHistoryError("path must be a project-relative file", "INVALID_REQUEST")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or "." in candidate.parts:
        raise DocumentHistoryError("path must be a normalized relative file", "INVALID_REQUEST")
    root = _project_root(project_id).resolve()
    target = (root / candidate).resolve()
    if target != root and not target.is_relative_to(root):
        raise DocumentHistoryError("path escapes project root", "INVALID_REQUEST")
    return target, candidate.as_posix()


class DocumentHistory:
    """One bounded index per project/path, with immutable content blobs."""

    def __init__(self, root: Path | None = None):
        from openprogram.paths import get_state_dir
        self.root = Path(root) if root is not None else get_state_dir() / "project-file-history"
        self._guard = threading.RLock()

    def _dir(self, project_id: str, relative: str) -> Path:
        key = hashlib.sha256(f"{project_id}\0{relative}".encode()).hexdigest()
        return self.root / hashlib.sha256(project_id.encode()).hexdigest() / key

    def _index(self, directory: Path) -> Path:
        return directory / "index.json"

    def _load(self, directory: Path) -> dict:
        try:
            data = json.loads(self._index(directory).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "entries": [], "groups": {}}
        except (OSError, ValueError) as exc:
            raise DocumentHistoryError("document history is corrupt", "HISTORY_CORRUPT") from exc
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise DocumentHistoryError("document history is corrupt", "HISTORY_CORRUPT")
        return data

    def _save(self, directory: Path, data: dict) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f".index.{uuid.uuid4().hex}.tmp"
        with tmp.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp, self._index(directory))

    @staticmethod
    def _read_bounded(path: Path) -> tuple[bytes, int]:
        path = Path(path)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return b"", 0
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1:
            raise DocumentHistoryError("target must be an ordinary file", "INVALID_REQUEST")
        with path.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise DocumentHistoryError("content exceeds 64 MiB", "PAYLOAD_TOO_LARGE")
        return raw, stat.S_IMODE(info.st_mode)

    def publish(self, project_id: str, relative: str, content: bytes, *, editor_id: str = "manual",
                baseline_revision: str | None = None, idempotency_key: str | None = None,
                close: bool = False) -> dict:
        target, relative = resolve_document(project_id, relative)
        if not isinstance(content, bytes) or len(content) > MAX_BYTES:
            raise DocumentHistoryError("content exceeds 64 MiB", "PAYLOAD_TOO_LARGE")
        with self._guard:
            before, mode = self._read_bounded(target)
            before_revision = _digest(before)
            if baseline_revision and baseline_revision != before_revision:
                raise DocumentHistoryError("baseline revision does not match", "CONFLICT")
            return self._record(project_id, relative, before, content, mode,
                                editor_id=editor_id, idempotency_key=idempotency_key, close=close)

    def _record(self, project_id: str, relative: str, before: bytes, content: bytes, mode: int,
                *, editor_id: str, idempotency_key: str | None, close: bool) -> dict:
        """Record a change after a caller has performed its atomic write."""
        before_revision = _digest(before)
        after_revision = _digest(content)
        directory = self._dir(project_id, relative)
        data = self._load(directory)
        for entry in data["entries"]:
            if idempotency_key and entry.get("idempotency_key") == idempotency_key:
                if entry.get("after_revision") != after_revision:
                    raise DocumentHistoryError("idempotency key payload conflict", "CONFLICT")
                return {**entry, "replayed": True}
        now = time.time()
        previous = data["entries"][-1] if data["entries"] else None
        same_group = (previous and previous.get("editor_id") == editor_id
                      and now - float(previous.get("group_started_at", now)) < GROUP_SECONDS
                      and previous.get("after_revision") == before_revision and not close)
        version_id = uuid.uuid4().hex
        blob = directory / f"{version_id}.bin"
        before_blob = directory / f"{version_id}.before.bin"
        tmp = directory / f".{version_id}.tmp"
        directory.mkdir(parents=True, exist_ok=True)
        with before_blob.open("xb") as handle:
            handle.write(before); handle.flush(); os.fsync(handle.fileno())
        with tmp.open("xb") as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp, blob)
        if same_group:
            previous.update({"after_revision": after_revision, "created_at": now,
                             "idempotency_key": idempotency_key, "blob": blob.name})
            self._save(directory, data)
            return previous
        entry = {
            "version_id": version_id, "project_id": project_id, "path": relative,
            "before_revision": before_revision, "after_revision": after_revision,
            "mode": mode, "editor_id": editor_id,
            "group_started_at": previous.get("group_started_at", now) if same_group else now,
            "created_at": now, "idempotency_key": idempotency_key,
            "blob": blob.name, "before_blob": before_blob.name,
        }
        data["entries"].append(entry)
        self._save(directory, data)
        return entry

    def list(self, project_id: str, relative: str, *, limit: int = 50, cursor: int = 0) -> dict:
        _, relative = resolve_document(project_id, relative)
        data = self._load(self._dir(project_id, relative))
        limit = max(1, min(int(limit), 100)); cursor = max(0, int(cursor))
        rows = list(reversed(data["entries"]))
        page = rows[cursor:cursor + limit]
        return {"entries": page, "next_cursor": str(cursor + limit) if cursor + limit < len(rows) else None}

    def content(self, project_id: str, relative: str, version_id: str, side: str = "after") -> bytes:
        _, relative = resolve_document(project_id, relative)
        data = self._load(self._dir(project_id, relative))
        entry = next((x for x in data["entries"] if x.get("version_id") == version_id), None)
        if entry is None:
            raise DocumentHistoryError("version not found", "NOT_FOUND")
        if side == "after":
            try: return (self._dir(project_id, relative) / entry["blob"]).read_bytes()
            except OSError as exc: raise DocumentHistoryError("version unavailable", "HISTORY_CORRUPT") from exc
        if side == "before":
            try: return (self._dir(project_id, relative) / entry["before_blob"]).read_bytes()
            except (KeyError, OSError) as exc: raise DocumentHistoryError("version unavailable", "HISTORY_CORRUPT") from exc
        raise DocumentHistoryError("side must be before or after", "INVALID_REQUEST")

    def restore(self, project_id: str, relative: str, version_id: str, *, side: str,
                baseline_revision: str, idempotency_key: str, editor_id: str = "manual") -> dict:
        raw = self.content(project_id, relative, version_id, side)
        target, _ = resolve_document(project_id, relative)
        current, mode = self._read_bounded(target)
        existing = self._load(self._dir(project_id, relative))["entries"]
        for entry in existing:
            if entry.get("idempotency_key") == idempotency_key:
                if entry.get("after_revision") != _digest(raw):
                    raise DocumentHistoryError("idempotency key payload conflict", "CONFLICT")
                return {**entry, "replayed": True}
        if _digest(current) != baseline_revision:
            raise DocumentHistoryError("baseline revision does not match", "CONFLICT")
        tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        with tmp.open("xb") as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp, target)
        try:
            return self._record(project_id, relative, current, raw, mode,
                                editor_id=editor_id, idempotency_key=idempotency_key, close=True)
        except DocumentHistoryError as exc:
            raise DocumentHistoryError(f"file restored but history recording failed: {exc}",
                                       "RECOVERY_REQUIRED") from exc
