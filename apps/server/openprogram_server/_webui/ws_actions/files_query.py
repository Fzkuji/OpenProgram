"""Project-file query and snapshot implementation.

This module owns bounded tree/search/read query state; mutation orchestration
remains in files.py.
"""
from __future__ import annotations

import errno
import json
import os
import posixpath
import secrets
import stat
import threading
import time
from dataclasses import dataclass

from . import files_shared as _shared
from .files_shared import _resolve

def _setting(name: str):
    """Read limits from the dependency-free shared configuration layer."""
    return getattr(_shared, name)


_QUERY_PAGE_SIZE = _shared._QUERY_PAGE_SIZE
_QUERY_MAX_SNAPSHOTS = _shared._QUERY_MAX_SNAPSHOTS
_QUERY_MAX_SNAPSHOT_ITEMS = _shared._QUERY_MAX_SNAPSHOT_ITEMS
_QUERY_MAX_TOTAL_ITEMS = _shared._QUERY_MAX_TOTAL_ITEMS
_QUERY_MAX_TOTAL_BYTES = _shared._QUERY_MAX_TOTAL_BYTES
_QUERY_MAX_CURSORS = _shared._QUERY_MAX_CURSORS
_QUERY_SNAPSHOT_TTL = _shared._QUERY_SNAPSHOT_TTL
_SEARCH_IGNORED_DIRS = _shared._SEARCH_IGNORED_DIRS

@dataclass(frozen=True)
class _QuerySnapshot:
    snapshot_id: str
    kind: str
    project_id: str
    path: str
    query: str
    mode: str
    entry_type: str
    sort: str
    basis: tuple
    rows: tuple
    created_at: float


_QUERY_SNAPSHOTS: dict[str, _QuerySnapshot] = {}
_QUERY_CURSORS: dict[str, tuple[str, int]] = {}
_QUERY_CURSOR_TOKENS: dict[tuple[str, int], str] = {}
_QUERY_LOCK = threading.RLock()


class _UnsafeQueryPath(ValueError):
    pass


class _QueryLimitError(OSError):
    pass


def _query_path(path: object) -> tuple[str | None, str | None]:
    """Return a canonical project-relative path for query actions."""
    if not isinstance(path, str) or os.path.isabs(path) or "\x00" in path:
        return None, "path escapes project root"
    normalized = os.path.normpath(path or "")
    if normalized in ("", "."):
        return "", None
    if normalized == ".." or normalized.startswith(".." + os.sep):
        return None, "path escapes project root"
    return normalized, None


def _query_page_size(value: object) -> int:
    if value is None:
        return _setting("_QUERY_PAGE_SIZE")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("page_size must be an integer")
    if not 1 <= value <= _setting("_QUERY_PAGE_SIZE"):
        raise ValueError(
            f"page_size must be between 1 and {_setting('_QUERY_PAGE_SIZE')}"
        )
    return value


def _query_error(project_id: str, path: str, *, code: str,
                 message: str | None = None, kind: str = "directory") -> dict:
    status = "stale" if code in {"STALE_SNAPSHOT", "STALE_CURSOR", "CURSOR"} else "error"
    payload = {
        "project_id": project_id,
        "path": path,
        "snapshot_id": None,
        "cursor": None,
        "next_cursor": None,
        "page_size": _setting("_QUERY_PAGE_SIZE"),
        "error_code": code,
        "error": message or code,
        "status": status,
    }
    payload["entries" if kind == "directory" else "results"] = []
    return payload


def _snapshot_footprint(snapshot: _QuerySnapshot) -> tuple[int, int]:
    # ``basis`` is the complete candidate vector, while ``rows`` is only
    # the filtered projection. Count each candidate once in the item quota;
    # estimate bytes for both stored structures because both remain resident.
    basis_bytes = len(json.dumps(
        snapshot.basis, sort_keys=True, default=str, separators=(",", ":"),
    ))
    rows_bytes = sum(
        len(json.dumps(row, sort_keys=True, default=str, separators=(",", ":")))
        for row in snapshot.rows
    )
    return max(len(snapshot.basis), len(snapshot.rows)), basis_bytes + rows_bytes


def _snapshot_usage() -> tuple[int, int, int]:
    with _QUERY_LOCK:
        footprints = [_snapshot_footprint(snapshot)
                      for snapshot in _QUERY_SNAPSHOTS.values()]
        return (
            sum(items for items, _bytes in footprints),
            sum(_bytes for _items, _bytes in footprints),
            len(_QUERY_CURSORS),
        )


def _remember_snapshot(snapshot: _QuerySnapshot) -> None:
    new_items, new_bytes = _snapshot_footprint(snapshot)
    if (new_items > _setting("_QUERY_MAX_TOTAL_ITEMS")
            or new_bytes > _setting("_QUERY_MAX_TOTAL_BYTES")):
        raise _QueryLimitError
    with _QUERY_LOCK:
        now = time.monotonic()
        for snapshot_id, old in list(_QUERY_SNAPSHOTS.items()):
            if now - old.created_at >= _setting("_QUERY_SNAPSHOT_TTL"):
                _evict_snapshot(snapshot_id)
        used_items, used_bytes, _cursor_count = _snapshot_usage_locked()
        while _QUERY_SNAPSHOTS and (
            used_items + new_items > _setting("_QUERY_MAX_TOTAL_ITEMS")
            or used_bytes + new_bytes > _setting("_QUERY_MAX_TOTAL_BYTES")
        ):
            evicted = next(iter(_QUERY_SNAPSHOTS))
            old = _QUERY_SNAPSHOTS.get(evicted)
            if old is not None:
                old_items, old_bytes = _snapshot_footprint(old)
                used_items -= old_items
                used_bytes -= old_bytes
            _evict_snapshot(evicted)
        if (used_items + new_items > _setting("_QUERY_MAX_TOTAL_ITEMS")
                or used_bytes + new_bytes > _setting("_QUERY_MAX_TOTAL_BYTES")):
            raise _QueryLimitError
        _QUERY_SNAPSHOTS[snapshot.snapshot_id] = snapshot
        while len(_QUERY_SNAPSHOTS) > _setting("_QUERY_MAX_SNAPSHOTS"):
            evicted = next(iter(_QUERY_SNAPSHOTS))
            _evict_snapshot(evicted)


def _snapshot_usage_locked() -> tuple[int, int, int]:
    footprints = [_snapshot_footprint(snapshot)
                  for snapshot in _QUERY_SNAPSHOTS.values()]
    return (
        sum(items for items, _bytes in footprints),
        sum(_bytes for _items, _bytes in footprints),
        len(_QUERY_CURSORS),
    )


def _evict_snapshot(snapshot_id: str) -> None:
    _QUERY_SNAPSHOTS.pop(snapshot_id, None)
    for key, token in list(_QUERY_CURSOR_TOKENS.items()):
        if key[0] == snapshot_id:
            _QUERY_CURSOR_TOKENS.pop(key, None)
            _QUERY_CURSORS.pop(token, None)


def _new_cursor(snapshot_id: str, offset: int) -> str:
    with _QUERY_LOCK:
        key = (snapshot_id, offset)
        existing = _QUERY_CURSOR_TOKENS.get(key)
        if existing is not None:
            return existing
        while len(_QUERY_CURSORS) >= _setting("_QUERY_MAX_CURSORS"):
            candidates = [
                candidate for candidate in _QUERY_SNAPSHOTS
                if candidate != snapshot_id
            ]
            if not candidates:
                raise _QueryLimitError
            _evict_snapshot(candidates[0])
        token = secrets.token_urlsafe(24)
        _QUERY_CURSORS[token] = (snapshot_id, offset)
        _QUERY_CURSOR_TOKENS[key] = token
    return token


def _snapshot_for_cursor(cursor: object) -> tuple[_QuerySnapshot | None, int]:
    if not isinstance(cursor, str) or not cursor:
        return None, 0
    with _QUERY_LOCK:
        state = _QUERY_CURSORS.get(cursor)
        if state is None:
            return None, 0
        snapshot_id, offset = state
        snapshot = _QUERY_SNAPSHOTS.get(snapshot_id)
        if snapshot is None:
            return None, 0
        if time.monotonic() - snapshot.created_at >= _setting("_QUERY_SNAPSHOT_TTL"):
            _evict_snapshot(snapshot_id)
            return None, 0
        return snapshot, offset


def _directory_entries(target: str) -> list[dict]:
    entries: list[dict] = []
    with os.scandir(target) as it:
        for entry in it:
            try:
                stat_result = entry.stat(follow_symlinks=False)
                entries.append({
                    "name": entry.name,
                    "type": "dir" if entry.is_dir(follow_symlinks=False) else "file",
                    "size": stat_result.st_size,
                    "mtime": stat_result.st_mtime,
                })
            except OSError:
                continue
    entries.sort(key=lambda row: (
        0 if row["type"] == "dir" else 1,
        row["name"].casefold(),
        row["name"],
    ))
    return entries


def _directory_basis(target: str) -> tuple:
    basis = []
    with os.scandir(target) as it:
        for entry in it:
            try:
                stat_result = entry.stat(follow_symlinks=False)
                basis.append((
                    entry.name,
                    "dir" if entry.is_dir(follow_symlinks=False) else "file",
                    stat_result.st_dev,
                    stat_result.st_ino,
                    stat_result.st_size,
                    stat_result.st_mtime_ns,
                    stat_result.st_mode,
                ))
            except OSError:
                continue
    return tuple(sorted(basis, key=lambda row: (row[0].casefold(), row[0])))


def _project_info(project_id: str) -> tuple[str | None, str | None, str | None]:
    from openprogram.store.project import project_store as _projects

    project = _projects.get_project(project_id)
    if project is None or not getattr(project, "path", None):
        return None, None, f"unknown project {project_id!r}"
    root = os.path.realpath(os.path.expanduser(project.path))
    return root, getattr(project, "name", None) or project_id, None


def _query_ignored_path(path: str) -> bool:
    return any(part in _SEARCH_IGNORED_DIRS for part in path.split("/"))


def _directory_flags() -> int:
    return (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0))


def _open_query_dir(project_id: str, path: str) -> int:
    root, _name, error = _project_info(project_id)
    if error:
        raise FileNotFoundError(error)
    current = root
    for part in path.split("/") if path else ():
        current = os.path.join(current, part)
        try:
            path_stat = os.lstat(current)
        except OSError:
            break
        if stat.S_ISLNK(path_stat.st_mode):
            raise _UnsafeQueryPath("path contains a symbolic link")
    fd = os.open(root, _directory_flags())
    try:
        for part in path.split("/") if path else ():
            try:
                child = os.open(part, _directory_flags(), dir_fd=fd)
            except OSError as exc:
                if getattr(exc, "errno", None) == getattr(errno, "ELOOP", 40):
                    raise _UnsafeQueryPath("path contains a symbolic link") from exc
                raise
            os.close(fd)
            fd = child
        return fd
    except Exception:
        os.close(fd)
        raise


def _open_child_dir(parent_fd: int, name: str) -> int:
    try:
        return os.open(name, _directory_flags(), dir_fd=parent_fd)
    except OSError as exc:
        try:
            path_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError:
            raise exc
        if stat.S_ISLNK(path_stat.st_mode):
            raise OSError(errno.ELOOP, "directory replaced by symbolic link") from exc
        raise


def _open_relative_dir(root_fd: int, relative_dir: str) -> int:
    fd = os.dup(root_fd)
    try:
        for part in relative_dir.split("/") if relative_dir else ():
            child = _open_child_dir(fd, part)
            os.close(fd)
            fd = child
        return fd
    except Exception:
        os.close(fd)
        raise


def _directory_snapshot_fd(fd: int) -> tuple[list[dict], tuple]:
    entries: list[dict] = []
    basis = []
    with os.scandir(fd) as iterator:
        for entry in iterator:
            if len(entries) >= _setting("_QUERY_MAX_SNAPSHOT_ITEMS"):
                raise _QueryLimitError
            stat_result = entry.stat(follow_symlinks=False)
            kind = "dir" if entry.is_dir(follow_symlinks=False) else "file"
            entries.append({
                "name": entry.name,
                "type": kind,
                "size": stat_result.st_size,
                "mtime": stat_result.st_mtime,
            })
            basis.append((
                entry.name, kind, stat_result.st_dev, stat_result.st_ino,
                stat_result.st_size, stat_result.st_mtime_ns, stat_result.st_mode,
            ))
    entries.sort(key=lambda row: (
        0 if row["type"] == "dir" else 1,
        row["name"].casefold(), row["name"],
    ))
    return entries, tuple(sorted(basis, key=lambda row: (row[0].casefold(), row[0])))


def _directory_basis_fd(fd: int) -> tuple:
    basis = []
    with os.scandir(fd) as iterator:
        for entry in iterator:
            if len(basis) >= _setting("_QUERY_MAX_SNAPSHOT_ITEMS"):
                raise _QueryLimitError
            stat_result = entry.stat(follow_symlinks=False)
            basis.append((
                entry.name,
                "dir" if entry.is_dir(follow_symlinks=False) else "file",
                stat_result.st_dev, stat_result.st_ino, stat_result.st_size,
                stat_result.st_mtime_ns, stat_result.st_mode,
            ))
    return tuple(sorted(basis, key=lambda row: (row[0].casefold(), row[0])))


def _fs_query_failure(exc: OSError) -> tuple[str, str]:
    if isinstance(exc, _QueryLimitError):
        return "LIMIT_EXCEEDED", "snapshot is too large"
    if str(exc).startswith("unknown project"):
        return "NOT_FOUND", str(exc)
    if isinstance(exc, PermissionError):
        return "PERMISSION", "permission denied while reading project files"
    if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
        return "NOT_FOUND", "project path not found or is not a directory"
    return "IO_ERROR", "unable to read project files"


def _search_match(value: str, query: str, mode: str) -> tuple[bool, int]:
    if not query:
        return True, 0
    value = value.casefold()
    query = query.casefold()
    if mode == "exact":
        return value == query, 0
    if mode == "prefix":
        return value.startswith(query), 0
    if mode == "fuzzy":
        position = 0
        for char in query:
            position = value.find(char, position)
            if position < 0:
                return False, 0
            position += 1
        return True, 0
    if mode == "contains":
        return query in value, 0
    return False, 0


def _search_rows(project_id: str, path: str, query: str, mode: str,
                 entry_type: str) -> tuple[list[dict], tuple, tuple[str, str] | None]:
    root, project_name, error = _project_info(project_id)
    if error:
        return [], (), ("NOT_FOUND", error)

    rows: list[tuple[int, dict]] = []
    basis: list[tuple] = []
    try:
        root_fd = _open_query_dir(project_id, path)
    except _UnsafeQueryPath as exc:
        return [], (), ("INVALID_REQUEST", str(exc))
    except OSError as exc:
        code, message = _fs_query_failure(exc)
        return [], (), (code, message)

    pending = [path]
    try:
      while pending:
        relative_dir = pending.pop()
        try:
            directory_fd = _open_relative_dir(root_fd, relative_dir)
        except _UnsafeQueryPath as exc:
            raise OSError(errno.ELOOP, str(exc)) from exc
        try:
            with os.scandir(directory_fd) as directory_entries:
                children = sorted(
                    directory_entries,
                    key=lambda entry: (entry.name.casefold(), entry.name),
                )
        except OSError:
            raise
        try:
            for entry in children:
                if entry.is_symlink():
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
                if not (is_dir or is_file):
                    continue
                rel = f"{relative_dir}/{entry.name}" if relative_dir else entry.name
                stat_result = entry.stat(follow_symlinks=False)
                kind = "dir" if is_dir else "file"
                basis.append((
                    rel, kind, stat_result.st_dev, stat_result.st_ino,
                    stat_result.st_size, stat_result.st_mtime_ns,
                    stat_result.st_mode,
                ))
                if len(basis) > _setting("_QUERY_MAX_SNAPSHOT_ITEMS"):
                    raise _QueryLimitError
                if is_dir and entry.name in _SEARCH_IGNORED_DIRS:
                    continue
                if entry_type != "all" and entry_type != kind:
                    if is_dir:
                        pending.append(rel)
                    continue
                name_match, _name_rank = _search_match(entry.name, query, mode)
                path_match, path_rank = _search_match(rel, query, mode)
                if name_match or path_match:
                    rank = 0 if name_match and query and entry.name.casefold() == query.casefold() else (
                        1 if name_match and query and entry.name.casefold().startswith(query.casefold()) else (
                            2 if name_match else 3 + path_rank
                        )
                    )
                    rows.append((rank, {
                        "name": entry.name,
                        "path": rel,
                        "type": kind,
                        "size": stat_result.st_size,
                        "mtime": stat_result.st_mtime,
                        "project_id": project_id,
                        "project_name": project_name,
                    }))
                if is_dir:
                    pending.append(rel)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        code, message = _fs_query_failure(exc)
        return [], (), (code, message)
    finally:
        os.close(root_fd)
    rows.sort(key=lambda item: (item[0], item[1]["path"].casefold(), item[1]["path"]))
    return [row for _rank, row in rows], tuple(sorted(basis)), None


def _query_page(snapshot: _QuerySnapshot, offset: int, page_size: int,
                field: str, cursor: str | None = None) -> dict:
    kind = "directory" if field == "entries" else "search"
    with _QUERY_LOCK:
        active = _QUERY_SNAPSHOTS.get(snapshot.snapshot_id)
        if active is not snapshot or (
            time.monotonic() - snapshot.created_at >= _setting("_QUERY_SNAPSHOT_TTL")
        ):
            return _query_error(snapshot.project_id, snapshot.path,
                                code="STALE_SNAPSHOT", kind=kind)
        rows = snapshot.rows[offset:offset + page_size]
        next_cursor = None
        try:
            if offset + len(rows) < len(snapshot.rows):
                next_cursor = _new_cursor(
                    snapshot.snapshot_id, offset + len(rows),
                )
                if _QUERY_SNAPSHOTS.get(snapshot.snapshot_id) is not snapshot:
                    return _query_error(snapshot.project_id, snapshot.path,
                                        code="STALE_SNAPSHOT", kind=kind)
        except _QueryLimitError:
            if cursor is None:
                _evict_snapshot(snapshot.snapshot_id)
            return _query_error(snapshot.project_id, snapshot.path,
                                code="LIMIT_EXCEEDED", kind=kind)
        return {
            "project_id": snapshot.project_id,
            "path": snapshot.path,
            "snapshot_id": snapshot.snapshot_id,
            "cursor": cursor,
            "next_cursor": next_cursor,
            "page_size": page_size,
            "total": len(snapshot.rows),
            "sort": snapshot.sort,
            "error_code": None,
            "status": "ready",
            field: list(rows),
        }


def _tree_query(project_id: str, path: object, page_size: object,
                cursor: object, snapshot_id: object, sort: object) -> dict:
    canonical_path, path_error = _query_path(path)
    if path_error:
        return _query_error(project_id, "", code="INVALID_REQUEST",
                            message=path_error)
    if _query_ignored_path(canonical_path or ""):
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="path is ignored")
    sort_name = sort if isinstance(sort, str) and sort else "dirs_first_path"
    if sort == "":
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="sort must not be empty")
    if not isinstance(sort, str) and sort is not None:
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="sort must be a string")
    if sort_name != "dirs_first_path":
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="unsupported sort")
    try:
        size = _query_page_size(page_size)
    except ValueError as exc:
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message=str(exc))
    if not isinstance(project_id, str) or not project_id:
        return _query_error(project_id or "", canonical_path or "",
                            code="INVALID_REQUEST", message="project_id is required")
    if cursor is not None and (not isinstance(cursor, str) or not cursor):
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="cursor must be a string")
    if snapshot_id is not None and (
        not isinstance(snapshot_id, str) or not snapshot_id
    ):
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="snapshot_id must be a string")
    if cursor is not None:
        snapshot, offset = _snapshot_for_cursor(cursor)
        if snapshot is None or snapshot.kind != "directory" or (
            snapshot.project_id != project_id
            or snapshot.path != canonical_path
            or snapshot.sort != sort_name
            or (snapshot_id is not None and snapshot.snapshot_id != snapshot_id)
        ):
            return _query_error(project_id, canonical_path or "",
                                code="STALE_SNAPSHOT", kind="directory")
        try:
            fd = _open_query_dir(project_id, canonical_path or "")
        except _UnsafeQueryPath as exc:
            return _query_error(project_id, canonical_path or "",
                                code="INVALID_REQUEST", message=str(exc))
        except OSError as exc:
            code, message = _fs_query_failure(exc)
            return _query_error(project_id, canonical_path or "", code=code,
                                message=message, kind="directory")
        try:
            basis = _directory_basis_fd(fd)
        except OSError as exc:
            code, message = _fs_query_failure(exc)
            return _query_error(project_id, canonical_path or "", code=code,
                                message=message, kind="directory")
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        if basis != snapshot.basis:
            return _query_error(project_id, canonical_path or "",
                                code="STALE_SNAPSHOT", kind="directory")
        return _query_page(snapshot, offset, size, "entries", str(cursor))

    try:
        fd = _open_query_dir(project_id, canonical_path or "")
    except _UnsafeQueryPath as exc:
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message=str(exc))
    except OSError as exc:
        code, message = _fs_query_failure(exc)
        return _query_error(project_id, canonical_path or "", code=code,
                            message=message, kind="directory")
    try:
        entries, basis = _directory_snapshot_fd(fd)
    except OSError as exc:
        code, message = _fs_query_failure(exc)
        return _query_error(project_id, canonical_path or "", code=code,
                            message=message, kind="directory")
    finally:
        os.close(fd)
    if len(entries) > _setting("_QUERY_MAX_SNAPSHOT_ITEMS"):
        return _query_error(project_id, canonical_path or "",
                            code="LIMIT_EXCEEDED", message="snapshot is too large",
                            kind="directory")
    snapshot = _QuerySnapshot(
        snapshot_id=secrets.token_urlsafe(18), kind="directory",
        project_id=project_id, path=canonical_path or "", query="", mode="",
        entry_type="all", sort=sort_name, basis=basis, rows=tuple(entries),
        created_at=time.monotonic(),
    )
    try:
        _remember_snapshot(snapshot)
    except _QueryLimitError:
        return _query_error(project_id, canonical_path or "",
                            code="LIMIT_EXCEEDED", message="snapshot is too large",
                            kind="directory")
    return _query_page(snapshot, 0, size, "entries")


def _search_query(project_id: str, path: object, query: object,
                  mode: object, entry_type: object, page_size: object,
                  cursor: object, snapshot_id: object, sort: object) -> dict:
    canonical_path, path_error = _query_path(path)
    if path_error:
        return _query_error(project_id, "", code="INVALID_REQUEST",
                            message=path_error, kind="search")
    if not isinstance(project_id, str) or not project_id:
        return _query_error(project_id or "", canonical_path or "",
                            code="INVALID_REQUEST", message="project_id is required",
                            kind="search")
    if not isinstance(query, str) or not query.strip():
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="query is required",
                            kind="search")
    if mode is None:
        mode_name = "contains"
    elif isinstance(mode, str):
        mode_name = mode
    else:
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="mode must be a string",
                            kind="search")
    if entry_type is None:
        type_name = "all"
    elif isinstance(entry_type, str):
        type_name = entry_type
    else:
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="type must be a string",
                            kind="search")
    if sort is None:
        sort_name = "rank_path"
    elif isinstance(sort, str):
        sort_name = sort
    else:
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="sort must be a string",
                            kind="search")
    query_text = query
    if _query_ignored_path(canonical_path or ""):
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="path is ignored",
                            kind="search")
    if mode_name not in {"contains", "prefix", "exact", "fuzzy"}:
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="unsupported match mode",
                            kind="search")
    if type_name not in {"all", "file", "dir"}:
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="unsupported entry type",
                            kind="search")
    if sort_name != "rank_path":
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="unsupported sort",
                            kind="search")
    try:
        size = _query_page_size(page_size)
    except ValueError as exc:
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message=str(exc), kind="search")
    if cursor is not None and (not isinstance(cursor, str) or not cursor):
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="cursor must be a string",
                            kind="search")
    if snapshot_id is not None and (
        not isinstance(snapshot_id, str) or not snapshot_id
    ):
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="snapshot_id must be a string",
                            kind="search")
    if cursor is not None:
        snapshot, offset = _snapshot_for_cursor(cursor)
        if snapshot is None or snapshot.kind != "project_search" or (
            snapshot.project_id != project_id
            or snapshot.path != canonical_path
            or snapshot.query != query_text
            or snapshot.mode != mode_name
            or snapshot.entry_type != type_name
            or snapshot.sort != sort_name
            or (snapshot_id is not None and snapshot.snapshot_id != snapshot_id)
        ):
            return _query_error(project_id, canonical_path or "",
                                code="STALE_SNAPSHOT", kind="search")
        rows, basis, error = _search_rows(
            project_id, canonical_path or "", query_text, mode_name, type_name,
        )
        if error:
            code, message = error
            return _query_error(project_id, canonical_path or "", code=code,
                                message=message, kind="search")
        if basis != snapshot.basis or rows != list(snapshot.rows):
            return _query_error(project_id, canonical_path or "",
                                code="STALE_SNAPSHOT", kind="search")
        return _query_page(snapshot, offset, size, "results", str(cursor))

    rows, basis, error = _search_rows(
        project_id, canonical_path or "", query_text, mode_name, type_name,
    )
    if error:
        code, message = error
        return _query_error(project_id, canonical_path or "", code=code,
                            message=message, kind="search")
    snapshot = _QuerySnapshot(
        snapshot_id=secrets.token_urlsafe(18), kind="project_search",
        project_id=project_id, path=canonical_path or "", query=query_text,
        mode=mode_name, entry_type=type_name, sort=sort_name, basis=basis,
        rows=tuple(rows), created_at=time.monotonic(),
    )
    if len(rows) > _setting("_QUERY_MAX_SNAPSHOT_ITEMS"):
        return _query_error(project_id, canonical_path or "",
                            code="LIMIT_EXCEEDED", message="snapshot is too large",
                            kind="search")
    try:
        _remember_snapshot(snapshot)
    except _QueryLimitError:
        return _query_error(project_id, canonical_path or "",
                            code="LIMIT_EXCEEDED", message="snapshot is too large",
                            kind="search")
    return _query_page(snapshot, 0, size, "results")


def _list_tree(project_id: str, path: str) -> dict:
    target, error = _resolve(project_id, path)
    if error:
        return {"entries": [], "error": error}
    if not os.path.isdir(target):
        return {"entries": [], "error": f"not a directory: {path!r}"}
    entries: list[dict] = []
    try:
        with os.scandir(target) as it:
            for entry in it:
                try:
                    stat = entry.stat(follow_symlinks=False)
                    entries.append({
                        "name": entry.name,
                        "type": "dir" if entry.is_dir() else "file",
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    })
                except OSError:
                    continue  # 坏符号链接等：跳过该项，不整体失败
    except OSError as e:
        return {"entries": [], "error": f"{type(e).__name__}: {e}"}
    entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, e["name"].lower()))
    return {"entries": entries}
