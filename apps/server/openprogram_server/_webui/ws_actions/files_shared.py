"""Shared primitives for canonical project-file WS actions.

Wire format::

    in:  {"action": "project_file_tree", "project_id": "...", "path": "",
          "page_size"?: 100, "cursor"?: "opaque"}
    out: {"type": "project_file_tree_result",
          "data": {"project_id", "path",
                   "entries": [{"name", "type": "file"|"dir", "size", "mtime"}],
                   "snapshot_id", "next_cursor", "total", "error"?}}

    in:  {"action": "project_file_search", "project_id": "...", "path": "",
          "query": "needle", "mode"?: "contains", "type"?: "all",
          "page_size"?: 100, "cursor"?: "opaque"}
    out: {"type": "project_file_search_result",
          "data": {"project_id", "path", "results": [{"name", "path",
                   "type": "file"|"dir", "size", "mtime"}],
                   "snapshot_id", "next_cursor", "total", "error"?}}

    in:  {"action": "project_file_read", "project_id": "...", "path": "src/x.py"}
    out: {"type": "project_file_read_result",
          "data": {"project_id", "path", "content"?, "size", "mtime", "revision"?,
                   "truncated"?, "binary"?, "too_large"?, "error"?}}

    in:  {"action": "project_file_write", "project_id": "...",
          "path": "src/x.py", "content": "...", "expected_mtime"?: 123.4}
    out: {"type": "project_file_write_result",
          "data": {"project_id", "path", "ok"?, "mtime"?, "revision"?,
                   "conflict"?: true, "error"?}}

    in:  {"action": "project_file_create", "project_id": "...",
          "path": "src/new.py", "kind": "file"|"dir"}
    out: {"type": "project_file_create_result",
          "data": {"project_id", "path", "kind", "ok"?, "error"?}}

    in:  {"action": "project_file_rename", "project_id": "...",
          "path": "old.py", "new_path": "sub/new.py"}   # rename AND move
    out: {"type": "project_file_rename_result",
          "data": {"project_id", "path", "new_path", "ok"?, "error"?}}

    in:  {"action": "project_file_copy", "project_id": "...",
          "path": "a.py", "new_path": "b.py"}   # copy2 file / copytree dir
    out: {"type": "project_file_copy_result",
          "data": {"project_id", "path", "new_path", "ok"?, "error"?}}

    in:  {"action": "project_file_delete", "project_id": "...", "path": "a.py"}
    out: {"type": "project_file_delete_result",
          "data": {"project_id", "path", "ok"?, "error"?}}
    # unlink file / rmtree dir (UI confirms first); project root refused.

    in:  {"action": "project_file_reveal", "project_id": "...", "path": "a.py"}
    out: {"type": "project_file_reveal_result",
          "data": {"project_id", "path", "ok"?, "error"?}}
    # Opens the OS file manager selecting the entry; never blocks,
    # launch failures come back as ``error``, never raised.

``path`` is always project-relative ("" = project root). Directory entries are
sorted dirs-first, then files, each alphabetically (case-insensitive), and
are paginated at 100 entries. Directory and recursive project search pages
use immutable snapshots and opaque cursors; changing the candidate basis or
any cursor-bound query property returns ``STALE_SNAPSHOT``. Search matches
names and project-relative paths without requiring the tree to be expanded.
It skips ignored build directories and never follows symlinks. Dotfiles are
included. Reads are capped at 1 MB (beyond → no content,
``too_large``) and binary files (NUL byte in the first 8 KiB) return
``binary`` instead of content.

Writes are text-only (utf-8), capped at 5 MB, require the parent
directory to exist (no mkdir), and — when ``expected_mtime`` is given —
refuse with ``conflict`` if the on-disk mtime differs (or the file is
gone), so the UI can offer a reload instead of clobbering.

Every path — including the HTTP ``/files/raw`` route in server.py —
goes through :func:`_resolve`, which rejects unknown projects, any
absolute ``path`` (even one pointing inside the root), and any path
whose realpath escapes the project root (``..``, symlinks pointing
outside).
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import errno
import hashlib
import json
import os
import posixpath
import secrets
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid
from builtins import open
from dataclasses import dataclass

_FILE_OPENER = open


def _open(*args, **kwargs):
    return _FILE_OPENER(*args, **kwargs)

# Hard cap on a single text read — the panel shows sources, not dumps.
_READ_MAX_BYTES = 1_000_000  # 1 MB
_BINARY_SNIFF_BYTES = 8192
# Writes come from the in-browser editor; 5 MB is far past any file a
# human edits in a textarea.
_WRITE_MAX_BYTES = 5_000_000  # 5 MB
_IDENTITY_DIGEST_MAX_BYTES = 256 * 1024
# Editable text reads are capped at 1 MB; their baseline digest must cover
# the same range. Mutation witnesses stay separately bounded below.
_READ_DIGEST_MAX_BYTES = _READ_MAX_BYTES

_QUERY_PAGE_SIZE = 100
_QUERY_MAX_SNAPSHOTS = 256
_QUERY_MAX_SNAPSHOT_ITEMS = 10_000
_QUERY_MAX_TOTAL_ITEMS = 50_000
_QUERY_MAX_TOTAL_BYTES = 16 * 1024 * 1024
_QUERY_MAX_CURSORS = 100_000
_QUERY_SNAPSHOT_TTL = 300.0
_SEARCH_IGNORED_DIRS = frozenset({
    "node_modules", ".git", "dist", ".next", "__pycache__",
    ".venv", "venv", ".cache", "target", "build",
})


def _resolve(project_id: str, path: str) -> tuple[str | None, str | None]:
    """Resolve a project-relative path without importing an action module."""
    path = path or ""
    if os.path.isabs(path):
        return None, "path escapes project root"
    from openprogram.store.project import project_store as _projects
    proj = _projects.get_project(project_id)
    if proj is None or not proj.path:
        return None, f"unknown project {project_id!r}"
    root = os.path.realpath(os.path.expanduser(proj.path))
    target = os.path.realpath(os.path.join(root, path))
    if target != root and not target.startswith(root + os.sep):
        return None, "path escapes project root"
    return target, None
_MUTATION_LOCKS: dict[str, threading.RLock] = {}
_MUTATION_LOCKS_GUARD = threading.Lock()
_ACTIVE_OPERATION_IDS: set[str] = set()
_ACTIVE_OPERATION_IDS_LOCK = threading.Lock()


def _mutation_lock(project_id: str) -> threading.RLock:
    with _MUTATION_LOCKS_GUARD:
        return _MUTATION_LOCKS.setdefault(project_id, threading.RLock())


@contextmanager
def _workspace_mutation_lock(project_id: str):
    """Serialize mutations across threads and worker processes."""
    local = _mutation_lock(project_id)
    with local:
        lock_file = None
        try:
            from openprogram.paths import get_state_dir
            state = get_state_dir()
            state.mkdir(parents=True, exist_ok=True)
            try:
                state.chmod(0o700)
            except OSError:
                pass
            lock_path = state / "file_operations.lock"
            lock_file = open(lock_path, "a+b")
            try:
                os.chmod(lock_path, 0o600)
            except OSError:
                pass
            try:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            yield
        finally:
            if lock_file is not None:
                try:
                    import fcntl
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                except (ImportError, OSError):
                    pass
                lock_file.close()


def _file_digest(target: str) -> str | None:
    try:
        if os.stat(target).st_size > _READ_DIGEST_MAX_BYTES:
            return None
        with _open(target, "rb") as stream:
            digest = hashlib.sha256()
            total = 0
            while total <= _READ_DIGEST_MAX_BYTES:
                chunk = stream.read(min(1024 * 1024, _READ_DIGEST_MAX_BYTES + 1 - total))
                if not chunk:
                    return digest.hexdigest()
                digest.update(chunk)
                total += len(chunk)
                if total > _READ_DIGEST_MAX_BYTES:
                    return None
            return None
    except OSError:
        return None


def _identity(project_id: str, path: str) -> dict:
    target, error = _resolve(project_id, path)
    if error or target is None:
        return {"exists": False, "error": error}
    try:
        info = os.stat(target, follow_symlinks=False)
    except OSError:
        return {"exists": False}
    kind = "dir" if stat.S_ISDIR(info.st_mode) else "file"
    identity = {
        "exists": True, "kind": kind, "dev": info.st_dev, "ino": info.st_ino,
        "mtime_ns": info.st_mtime_ns,
        "size": info.st_size,
    }
    if kind == "file" and info.st_size <= _IDENTITY_DIGEST_MAX_BYTES:
        identity["digest"] = _file_digest(target)
    return identity


def _identity_matches(actual: dict, expected: dict) -> bool:
    if bool(actual.get("exists")) != bool(expected.get("exists")):
        return False
    if not actual.get("exists"):
        return True
    return all(field not in expected or actual.get(field) == expected.get(field)
               for field in ("kind", "dev", "ino", "mtime_ns", "size", "digest"))


def _mutation_states(project_id: str, action: str, payload: dict) -> tuple[dict, dict]:
    path = str(payload.get("path") or "")
    before = {"path": path, "source": _identity(project_id, path)}
    if action == "project_file_write":
        digest = payload.get("content_sha256")
        if not isinstance(digest, str):
            raw = str(payload.get("content") or "").encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
        after = {"path": path, "target": {
            "exists": True, "kind": "file", "digest": digest,
        }}
        byte_length = payload.get("content_byte_length")
        if isinstance(byte_length, int) and byte_length >= 0:
            after["target"]["size"] = byte_length
    elif action == "project_file_create":
        kind = payload.get("kind")
        after = {"path": path, "target": {"exists": True, "kind": kind,
                                               "digest": hashlib.sha256(b"").hexdigest()
                                               if kind == "file" else None}}
    elif action in {"project_file_rename", "project_file_copy"}:
        destination = str(payload.get("new_path") or "")
        before["destination"] = _identity(project_id, destination)
        source = before["source"]
        target = {"exists": True, "kind": source.get("kind"),
                  "digest": source.get("digest")}
        if source.get("kind") == "file":
            target["size"] = source.get("size")
        # rename preserves the filesystem identity; copy intentionally does
        # not.  Its durable proof is the destination kind/content digest.
        if action == "project_file_rename":
            target.update(dev=source.get("dev"), ino=source.get("ino"))
        after = {"path": path, "destination": destination, "target": target}
    else:
        after = {"path": path, "target": {"exists": False}}
    return before, after


def _canonical_mutation_payload(payload: dict) -> dict:
    canonical = dict(payload)
    for field in ("path", "new_path"):
        value = canonical.get(field)
        if isinstance(value, str):
            normalized = posixpath.normpath(value.replace("\\", "/"))
            canonical[field] = "" if normalized == "." else normalized
    if isinstance(canonical.get("content"), str):
        raw = canonical.pop("content").encode("utf-8")
        canonical["content_sha256"] = hashlib.sha256(raw).hexdigest()
        canonical["content_byte_length"] = len(raw)
    return canonical


def _mutation_state_matches(project_id: str, action: str, payload: dict,
                            state: dict, *, after: bool) -> bool:
    path = str(payload.get("path") or "")
    if action == "project_file_write":
        actual = _identity(project_id, path)
        return _identity_matches(actual, state.get("target", {})) if after else _identity_matches(actual, state.get("source", {}))
    if action == "project_file_create":
        actual = _identity(project_id, path)
        return _identity_matches(actual, state.get("target", {})) if after else not actual.get("exists")
    if action == "project_file_delete":
        actual = _identity(project_id, path)
        return not actual.get("exists") if after else _identity_matches(actual, state.get("source", {}))
    destination = str(payload.get("new_path") or "")
    source = _identity(project_id, path)
    target = _identity(project_id, destination)
    if after:
        expected = state.get("target", {})
        source_ok = (
            not source.get("exists")
            if action == "project_file_rename"
            else _identity_matches(source, state.get("source", {}))
        )
        return source_ok and _identity_matches(target, expected)
    return _identity_matches(source, state.get("source", {})) and not target.get("exists")


def _replayed_mutation_result(project_id: str, action: str, payload: dict) -> dict:
    result = {"ok": True}
    if action == "project_file_write":
        target, _error = _resolve(project_id, str(payload.get("path") or ""))
        try:
            result["mtime"] = os.stat(target).st_mtime if target else None
        except OSError:
            result["mtime"] = None
    return result


def _normalise_mutation_result(result: dict) -> dict:
    result = dict(result)
    if result.get("status") == "recovery_required":
        return result
    if result.get("ok"):
        result.setdefault("status", "ready")
        return result
    if result.get("conflict"):
        result.setdefault("status", "conflict")
        result.setdefault("error_code", "CONFLICT")
        return result
    if result.get("error"):
        result.setdefault("status", "error")
        message = str(result["error"]).lower()
        if ("must be" in message or "required" in message
                or "escapes" in message or "invalid" in message):
            code = "INVALID_REQUEST"
        elif "permission" in message:
            code = "PERMISSION"
        elif ("does not exist" in message or "unknown project" in message
              or "not a " in message):
            code = "NOT_FOUND"
        elif "already exists" in message or "changed on disk" in message:
            code = "CONFLICT"
        else:
            code = "IO_ERROR"
        result.setdefault("error_code", code)
        if code == "CONFLICT":
            result["status"] = "conflict"
    return result


def _normalise_file_result(result: dict) -> dict:
    """Give non-mutating file replies one stable terminal shape."""
    result = dict(result)
    if result.get("error"):
        result.setdefault("status", "error")
        message = str(result["error"]).lower()
        if ("must be" in message or "required" in message
                or "escapes" in message or "invalid" in message):
            code = "INVALID_REQUEST"
        elif "permission" in message:
            code = "PERMISSION"
        elif ("not found" in message or "does not exist" in message
              or "unknown project" in message or "not a " in message):
            code = "NOT_FOUND"
        else:
            code = "IO_ERROR"
        result.setdefault("error_code", code)
    else:
        result.setdefault("status", "ready")
    return result


def _request_id(cmd: dict) -> str | None:
    value = cmd.get("request_id")
    if not isinstance(value, str):
        return None
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        return None
    return str(parsed) if str(parsed) == value else None


def _process_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _owner_process_alive(row: dict) -> bool:
    pid = row.get("owner_pid")
    start = row.get("owner_process_start")
    if not isinstance(pid, int) or not isinstance(start, str) or not start:
        return False
    from openprogram.store.file_operations import (
        current_owner_identity, process_start_identity,
    )
    instance_id, current_pid, _current_start = current_owner_identity()
    # The in-process active-operation registry is authoritative for this
    # worker. A stale record left by an interrupted local task is recoverable
    # even though its PID and process-start token still identify this process.
    if row.get("owner_instance_id") == instance_id and pid == current_pid:
        return False
    return _process_alive(pid) and process_start_identity(pid) == start


def _durable_file_action(project_id: str, action: str, key: object,
                         payload: dict, fn):
    """Claim, execute, and persist one retry-safe file mutation."""
    if not isinstance(key, str) or not key:
        return fn()
    from openprogram.store.file_operations import (
        FileOperationConflict, default_file_operation_store, fingerprint,
    )
    store = default_file_operation_store()
    payload = _canonical_mutation_payload(payload)
    before, after = _mutation_states(project_id, action, payload)
    with _ACTIVE_OPERATION_IDS_LOCK:
        try:
            # Claim/read is intentionally outside the filesystem lock. A retry
            # of an active operation gets a terminal in-progress receipt
            # immediately. Holding this small registry lock through begin also
            # closes the gap before the owner is visible to same-process retry.
            row, owner = store.begin(
                project_id, action, key, fingerprint(payload),
                payload=payload, before=before, after=after,
            )
        except FileOperationConflict:
            return {"status": "conflict", "error_code": "IDEMPOTENCY_KEY_CONFLICT",
                    "error": "idempotency key is bound to another file operation"}
        if not owner:
            if row.get("status") in {"completed", "recovery_required", "conflict", "error"}:
                return store.replay(row)
            operation_id = row["operation_id"]
            if operation_id in _ACTIVE_OPERATION_IDS or _owner_process_alive(row):
                return {"status": "in_progress", "operation_id": operation_id}
            payload = json.loads(row.get("payload_json") or "{}")
            before = json.loads(row.get("before_json") or "{}")
            after = json.loads(row.get("after_json") or "{}")
            _ACTIVE_OPERATION_IDS.add(operation_id)
        else:
            operation_id = row["operation_id"]
            _ACTIVE_OPERATION_IDS.add(operation_id)

    try:
        with _workspace_mutation_lock(project_id):
            # Preconditions are checked after acquiring the shared lock. An
            # external writer between claim and apply therefore cannot be
            # mistaken for this operation.
            if not owner and not store.claim_recovery(operation_id):
                current = store.get(operation_id)
                if current and current.get("status") in {
                    "completed", "recovery_required", "conflict", "error",
                }:
                    return store.replay(current)
                return {"status": "in_progress", "operation_id": operation_id}
            if not store.owned_by_current_process(operation_id):
                current = store.get(operation_id)
                if current and current.get("status") in {
                    "completed", "recovery_required", "conflict", "error",
                }:
                    return store.replay(current)
                return {"status": "in_progress", "operation_id": operation_id}
            if not _mutation_state_matches(project_id, action, payload, before, after=False):
                result = {"status": "recovery_required", "error_code": "RECOVERY_REQUIRED",
                          "error": "file operation state cannot be reconciled safely"}
                store.finish(operation_id, result, status="recovery_required",
                             phase="recovery_required")
                result["operation_id"] = operation_id
                return result
            store.mark_applying(operation_id)
            try:
                result = fn()
            except Exception as exc:
                # The journal is an intent record, not an in-memory lease.  A
                # handler exception must leave a terminal, explainable record so
                # a restart cannot expose a permanent in_flight operation.
                if _mutation_state_matches(project_id, action, payload, before, after=False):
                    result = {"error": f"{type(exc).__name__}: file operation failed",
                              "error_code": "IO_ERROR"}
                    terminal = "error"
                else:
                    result = {"status": "recovery_required",
                              "error_code": "RECOVERY_REQUIRED",
                              "error": "file operation state cannot be reconciled safely"}
                    terminal = "recovery_required"
                store.finish(operation_id, result, status=terminal, phase=terminal)
                result = _normalise_mutation_result(result)
                result["operation_id"] = operation_id
                return result
            result = _normalise_mutation_result(result)
            store.complete(operation_id, result)
            result = dict(result)
            result["operation_id"] = operation_id
            return result
    finally:
        with _ACTIVE_OPERATION_IDS_LOCK:
            _ACTIVE_OPERATION_IDS.discard(operation_id)
