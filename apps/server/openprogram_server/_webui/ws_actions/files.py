"""Project files panel WS actions — browse + read files under a project root.

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
          "data": {"project_id", "path", "content"?, "size", "mtime",
                   "truncated"?, "binary"?, "too_large"?, "error"?}}

    in:  {"action": "project_file_write", "project_id": "...",
          "path": "src/x.py", "content": "...", "expected_mtime"?: 123.4}
    out: {"type": "project_file_write_result",
          "data": {"project_id", "path", "ok"?, "mtime"?,
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
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass

# Hard cap on a single text read — the panel shows sources, not dumps.
_READ_MAX_BYTES = 1_000_000  # 1 MB
_BINARY_SNIFF_BYTES = 8192
# Writes come from the in-browser editor; 5 MB is far past any file a
# human edits in a textarea.
_WRITE_MAX_BYTES = 5_000_000  # 5 MB

_QUERY_PAGE_SIZE = 100
_QUERY_MAX_SNAPSHOTS = 256
_SEARCH_IGNORED_DIRS = frozenset({
    "node_modules", ".git", "dist", ".next", "__pycache__",
    ".venv", "venv", ".cache", "target", "build",
})


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


_QUERY_SNAPSHOTS: dict[str, _QuerySnapshot] = {}
_QUERY_CURSORS: dict[str, tuple[str, int]] = {}
_QUERY_LOCK = threading.RLock()


def _resolve(project_id: str, path: str) -> tuple[str | None, str | None]:
    """Resolve a project-relative ``path`` to an absolute filesystem path
    INSIDE the project root. Returns ``(absolute_path, error)``; exactly
    one side is non-None. Shared by both WS actions and ``/files/raw``.
    """
    path = path or ""
    # Absolute paths are never valid client input — reject up front, even
    # ones that would resolve inside the root.
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


def _query_path(path: object) -> tuple[str | None, str | None]:
    """Return a canonical project-relative path for query actions."""
    if path is None:
        return "", None
    if not isinstance(path, str) or os.path.isabs(path):
        return None, "path escapes project root"
    normalized = os.path.normpath(path or "")
    if normalized in ("", "."):
        return "", None
    if normalized == ".." or normalized.startswith(".." + os.sep):
        return None, "path escapes project root"
    return normalized, None


def _query_page_size(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return _QUERY_PAGE_SIZE
    return max(1, min(value, _QUERY_PAGE_SIZE))


def _query_error(project_id: str, path: str, *, code: str,
                 message: str | None = None, kind: str = "directory") -> dict:
    payload = {
        "project_id": project_id,
        "path": path,
        "snapshot_id": None,
        "cursor": None,
        "next_cursor": None,
        "page_size": _QUERY_PAGE_SIZE,
        "error_code": code,
        "error": message or code,
    }
    payload["entries" if kind == "directory" else "results"] = []
    return payload


def _remember_snapshot(snapshot: _QuerySnapshot) -> None:
    with _QUERY_LOCK:
        _QUERY_SNAPSHOTS[snapshot.snapshot_id] = snapshot
        while len(_QUERY_SNAPSHOTS) > _QUERY_MAX_SNAPSHOTS:
            evicted = next(iter(_QUERY_SNAPSHOTS))
            _QUERY_SNAPSHOTS.pop(evicted)
            for token, (snapshot_id, _offset) in list(_QUERY_CURSORS.items()):
                if snapshot_id == evicted:
                    _QUERY_CURSORS.pop(token, None)


def _new_cursor(snapshot_id: str, offset: int) -> str:
    token = secrets.token_urlsafe(24)
    with _QUERY_LOCK:
        _QUERY_CURSORS[token] = (snapshot_id, offset)
    return token


def _snapshot_for_cursor(cursor: object) -> tuple[_QuerySnapshot | None, int]:
    if not isinstance(cursor, str) or not cursor:
        return None, 0
    with _QUERY_LOCK:
        state = _QUERY_CURSORS.get(cursor)
        if state is None:
            return None, 0
        snapshot_id, offset = state
        return _QUERY_SNAPSHOTS.get(snapshot_id), offset


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
                 entry_type: str) -> tuple[list[dict], tuple, str | None]:
    root, project_name, error = _project_info(project_id)
    if error:
        return [], (), error
    target, error = _resolve(project_id, path)
    if error:
        return [], (), error
    if not os.path.isdir(target):
        return [], (), f"not a directory: {path!r}"

    rows: list[tuple[int, dict]] = []
    basis: list[tuple] = []
    pending = [target]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as directory_entries:
                children = sorted(
                    directory_entries,
                    key=lambda entry: (entry.name.casefold(), entry.name),
                )
        except OSError:
            continue
        for entry in children:
            try:
                if entry.is_symlink():
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
                if not (is_dir or is_file):
                    continue
                rel = os.path.relpath(entry.path, root).replace(os.sep, "/")
                stat_result = entry.stat(follow_symlinks=False)
                basis.append((
                    rel, "dir" if is_dir else "file", stat_result.st_dev,
                    stat_result.st_ino, stat_result.st_size,
                    stat_result.st_mtime_ns, stat_result.st_mode,
                ))
                if is_dir and entry.name in _SEARCH_IGNORED_DIRS:
                    continue
                kind = "dir" if is_dir else "file"
                if entry_type != "all" and entry_type != kind:
                    if is_dir:
                        pending.append(entry.path)
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
                    pending.append(entry.path)
            except OSError:
                continue
    rows.sort(key=lambda item: (item[0], item[1]["path"].casefold(), item[1]["path"]))
    return [row for _rank, row in rows], tuple(sorted(basis)), None


def _query_page(snapshot: _QuerySnapshot, offset: int, page_size: int,
                field: str, cursor: str | None = None) -> dict:
    rows = snapshot.rows[offset:offset + page_size]
    next_cursor = None
    if offset + len(rows) < len(snapshot.rows):
        next_cursor = _new_cursor(snapshot.snapshot_id, offset + len(rows))
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
        field: list(rows),
    }


def _tree_query(project_id: str, path: object, page_size: object,
                cursor: object, snapshot_id: object, sort: object) -> dict:
    canonical_path, path_error = _query_path(path)
    if path_error:
        return _query_error(project_id, "", code="INVALID_REQUEST",
                            message=path_error)
    sort_name = sort if isinstance(sort, str) and sort else "dirs_first_path"
    if sort_name != "dirs_first_path":
        return _query_error(project_id, canonical_path or "",
                            code="INVALID_REQUEST", message="unsupported sort")
    size = _query_page_size(page_size)
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
        target, error = _resolve(project_id, canonical_path or "")
        if error or target is None or not os.path.isdir(target):
            return _query_error(project_id, canonical_path or "",
                                code="STALE_SNAPSHOT", kind="directory")
        try:
            basis = _directory_basis(target)
        except OSError:
            basis = None
        if basis != snapshot.basis:
            return _query_error(project_id, canonical_path or "",
                                code="STALE_SNAPSHOT", kind="directory")
        return _query_page(snapshot, offset, size, "entries", str(cursor))

    target, error = _resolve(project_id, canonical_path or "")
    if error:
        code = "NOT_FOUND" if error.startswith("unknown project") else "INVALID_REQUEST"
        return _query_error(project_id, canonical_path or "", code=code,
                            message=error, kind="directory")
    if target is None or not os.path.isdir(target):
        return _query_error(project_id, canonical_path or "", code="NOT_FOUND",
                            message=f"not a directory: {canonical_path!r}",
                            kind="directory")
    try:
        entries = _directory_entries(target)
        basis = _directory_basis(target)
    except OSError as exc:
        return _query_error(project_id, canonical_path or "", code="NOT_FOUND",
                            message=f"{type(exc).__name__}: {exc}",
                            kind="directory")
    snapshot = _QuerySnapshot(
        snapshot_id=secrets.token_urlsafe(18), kind="directory",
        project_id=project_id, path=canonical_path or "", query="", mode="",
        entry_type="all", sort=sort_name, basis=basis, rows=tuple(entries),
    )
    _remember_snapshot(snapshot)
    return _query_page(snapshot, 0, size, "entries")


def _search_query(project_id: str, path: object, query: object,
                  mode: object, entry_type: object, page_size: object,
                  cursor: object, snapshot_id: object, sort: object) -> dict:
    canonical_path, path_error = _query_path(path)
    if path_error:
        return _query_error(project_id, "", code="INVALID_REQUEST",
                            message=path_error, kind="search")
    query_text = query if isinstance(query, str) else ""
    mode_name = mode if isinstance(mode, str) and mode else "contains"
    type_name = entry_type if isinstance(entry_type, str) and entry_type else "all"
    sort_name = sort if isinstance(sort, str) and sort else "rank_path"
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
    size = _query_page_size(page_size)
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
        if error or basis != snapshot.basis:
            return _query_error(project_id, canonical_path or "",
                                code="STALE_SNAPSHOT", kind="search")
        return _query_page(snapshot, offset, size, "results", str(cursor))

    rows, basis, error = _search_rows(
        project_id, canonical_path or "", query_text, mode_name, type_name,
    )
    if error:
        code = "NOT_FOUND" if error.startswith("unknown project") else "INVALID_REQUEST"
        return _query_error(project_id, canonical_path or "", code=code,
                            message=error, kind="search")
    snapshot = _QuerySnapshot(
        snapshot_id=secrets.token_urlsafe(18), kind="project_search",
        project_id=project_id, path=canonical_path or "", query=query_text,
        mode=mode_name, entry_type=type_name, sort=sort_name, basis=basis,
        rows=tuple(rows),
    )
    _remember_snapshot(snapshot)
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


def _read_file(project_id: str, path: str) -> dict:
    target, error = _resolve(project_id, path)
    if error:
        return {"error": error}
    if not os.path.isfile(target):
        return {"error": f"not a file: {path!r}"}
    try:
        stat = os.stat(target)
        result: dict = {"size": stat.st_size, "mtime": stat.st_mtime}
        with open(target, "rb") as f:
            head = f.read(_BINARY_SNIFF_BYTES)
            if b"\x00" in head:
                result["binary"] = True
                return result
            if stat.st_size > _READ_MAX_BYTES:
                result["too_large"] = True
                return result
            raw = head + f.read()
    except OSError as e:
        return {"error": f"{type(e).__name__}: {e}"}
    result["content"] = raw.decode("utf-8", errors="replace")
    return result


def _write_file(project_id: str, path: str, content: str,
                expected_mtime: float | None) -> dict:
    target, error = _resolve(project_id, path)
    if error:
        return {"error": error}
    raw = content.encode("utf-8")
    if len(raw) > _WRITE_MAX_BYTES:
        return {"error": "content exceeds 5 MB"}
    if os.path.isdir(target):
        return {"error": f"not a file: {path!r}"}
    if not os.path.isdir(os.path.dirname(target)):
        return {"error": f"parent directory does not exist for {path!r}"}
    if expected_mtime is not None:
        # Optimistic-concurrency gate: the editor sends the mtime it
        # read; any drift (or a vanished file) means someone else wrote
        # meanwhile — never clobber, let the UI offer a reload.
        try:
            if os.stat(target).st_mtime != expected_mtime:
                return {"conflict": True}
        except OSError:
            return {"conflict": True}
    try:
        # 原子替换：先写同目录临时文件再 os.replace——中途崩溃/磁盘满
        # 不会留下截断的目标文件。
        tmp = f"{target}.tmp.{os.getpid()}"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, target)
        return {"ok": True, "mtime": os.stat(target).st_mtime}
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return {"error": f"{type(e).__name__}: {e}"}


def _create_entry(project_id: str, path: str, kind: str) -> dict:
    if kind not in ("file", "dir"):
        return {"error": "kind must be 'file' or 'dir'"}
    target, error = _resolve(project_id, path)
    if error:
        return {"error": error}
    if not os.path.isdir(os.path.dirname(target)):
        return {"error": f"parent directory does not exist for {path!r}"}
    try:
        if kind == "dir":
            os.makedirs(target, exist_ok=False)
        else:
            with open(target, "x"):
                pass
    except FileExistsError:
        return {"error": f"already exists: {path!r}"}
    except OSError as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"ok": True}


def _rename_entry(project_id: str, path: str, new_path: str) -> dict:
    src, error = _resolve(project_id, path)
    if error:
        return {"error": error}
    dst, error = _resolve(project_id, new_path)
    if error:
        return {"error": error}
    if not os.path.exists(src):
        return {"error": f"source does not exist: {path!r}"}
    # Case-only rename (apple.txt → Apple.txt) on a case-insensitive
    # filesystem (macOS default): the destination "exists" because it
    # IS the source. Detect via samefile + case-only basename diff and
    # rename through a temporary sibling name — a direct rename is a
    # no-op on some such filesystems.
    src_base, dst_base = os.path.basename(src), os.path.basename(dst)
    case_only = (
        src != dst
        and src_base != dst_base
        and src_base.lower() == dst_base.lower()
        and os.path.exists(dst)
        and os.path.samefile(src, dst)
    )
    if os.path.exists(dst) and not case_only:
        return {"error": f"destination already exists: {new_path!r}"}
    try:
        if case_only:
            tmp = f"{src}.casetmp.{os.getpid()}"
            os.rename(src, tmp)
            try:
                os.rename(tmp, dst)
            except OSError:
                os.rename(tmp, src)  # roll back — never strand the file
                raise
        else:
            os.rename(src, dst)
    except OSError as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"ok": True}


def _copy_entry(project_id: str, path: str, new_path: str) -> dict:
    src, error = _resolve(project_id, path)
    if error:
        return {"error": error}
    dst, error = _resolve(project_id, new_path)
    if error:
        return {"error": error}
    if not os.path.exists(src):
        return {"error": f"source does not exist: {path!r}"}
    if os.path.exists(dst):
        return {"error": f"destination already exists: {new_path!r}"}
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    except OSError as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"ok": True}


def _delete_entry(project_id: str, path: str) -> dict:
    target, error = _resolve(project_id, path)
    if error:
        return {"error": error}
    # ``""``, ``"."``, ``"src/.."`` all resolve to the root — compare
    # resolved paths, not the raw string.
    root, _ = _resolve(project_id, "")
    if target == root:
        return {"error": "refusing to delete project root"}
    if not os.path.exists(target):
        return {"error": f"does not exist: {path!r}"}
    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.unlink(target)
    except OSError as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"ok": True}


def _reveal_entry(project_id: str, path: str) -> dict:
    target, error = _resolve(project_id, path)
    if error:
        return {"error": error}
    if not os.path.exists(target):
        return {"error": f"does not exist: {path!r}"}
    try:
        # Popen (never run/call): the file manager must not block the
        # executor thread. argv lists only — no shell.
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", target])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", "/select," + target])
        else:
            # No cross-desktop "select this file" verb on Linux — open
            # the containing directory instead.
            subprocess.Popen(["xdg-open",
                              target if os.path.isdir(target)
                              else os.path.dirname(target)])
    except OSError as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"ok": True}


async def handle_project_file_tree(ws, cmd: dict) -> None:
    raw_project_id = cmd.get("project_id")
    project_id = raw_project_id.strip() if isinstance(raw_project_id, str) else ""
    # No .strip(): filenames with leading/trailing whitespace must
    # round-trip so the echoed ``path`` matches the request.
    path = cmd.get("path") or ""
    loop = asyncio.get_event_loop()
    if "root" in cmd:
        result = _query_error(
            project_id, "", code="INVALID_REQUEST",
            message="root is not accepted; use project_id and relative path",
        )
        await ws.send_text(json.dumps({
            "type": "project_file_tree_result", "data": result,
        }, default=str))
        return
    result = await loop.run_in_executor(
        None, lambda: _tree_query(
            project_id, path, cmd.get("page_size"), cmd.get("cursor"),
            cmd.get("snapshot_id"), cmd.get("sort"),
        ),
    )
    await ws.send_text(json.dumps({
        "type": "project_file_tree_result",
        "data": result,
    }, default=str))


async def handle_project_file_search(ws, cmd: dict) -> None:
    raw_project_id = cmd.get("project_id")
    project_id = raw_project_id.strip() if isinstance(raw_project_id, str) else ""
    path = cmd.get("path") or ""
    loop = asyncio.get_event_loop()
    if "root" in cmd:
        result = _query_error(
            project_id, "", code="INVALID_REQUEST",
            message="root is not accepted; use project_id and relative path",
            kind="search",
        )
        await ws.send_text(json.dumps({
            "type": "project_file_search_result", "data": result,
        }, default=str))
        return
    result = await loop.run_in_executor(
        None, lambda: _search_query(
            project_id, path, cmd.get("query"), cmd.get("mode"),
            cmd.get("type"), cmd.get("page_size"), cmd.get("cursor"),
            cmd.get("snapshot_id"), cmd.get("sort"),
        ),
    )
    await ws.send_text(json.dumps({
        "type": "project_file_search_result",
        "data": result,
    }, default=str))


async def handle_project_file_read(ws, cmd: dict) -> None:
    project_id = (cmd.get("project_id") or "").strip()
    path = cmd.get("path") or ""  # no .strip() — see handle_project_file_tree
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: _read_file(project_id, path),
    )
    payload = {"project_id": project_id, "path": path}
    payload.update(result)
    await ws.send_text(json.dumps({
        "type": "project_file_read_result",
        "data": payload,
    }, default=str))


async def handle_project_file_write(ws, cmd: dict) -> None:
    project_id = (cmd.get("project_id") or "").strip()
    path = cmd.get("path") or ""  # no .strip() — see handle_project_file_tree
    content = cmd.get("content")
    expected_mtime = cmd.get("expected_mtime")
    if not isinstance(expected_mtime, (int, float)):
        expected_mtime = None
    if not isinstance(content, str):
        result: dict = {"error": "content must be a string"}
    else:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: _write_file(project_id, path, content, expected_mtime),
        )
    payload = {"project_id": project_id, "path": path}
    payload.update(result)
    await ws.send_text(json.dumps({
        "type": "project_file_write_result",
        "data": payload,
    }, default=str))


async def handle_project_file_create(ws, cmd: dict) -> None:
    project_id = (cmd.get("project_id") or "").strip()
    path = cmd.get("path") or ""  # no .strip() — see handle_project_file_tree
    kind = cmd.get("kind") or "file"
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: _create_entry(project_id, path, kind),
    )
    payload = {"project_id": project_id, "path": path, "kind": kind}
    payload.update(result)
    await ws.send_text(json.dumps({
        "type": "project_file_create_result",
        "data": payload,
    }, default=str))


async def handle_project_file_rename(ws, cmd: dict) -> None:
    project_id = (cmd.get("project_id") or "").strip()
    path = cmd.get("path") or ""  # no .strip() — see handle_project_file_tree
    new_path = cmd.get("new_path") or ""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: _rename_entry(project_id, path, new_path),
    )
    payload = {"project_id": project_id, "path": path, "new_path": new_path}
    payload.update(result)
    await ws.send_text(json.dumps({
        "type": "project_file_rename_result",
        "data": payload,
    }, default=str))


async def handle_project_file_copy(ws, cmd: dict) -> None:
    project_id = (cmd.get("project_id") or "").strip()
    path = cmd.get("path") or ""  # no .strip() — see handle_project_file_tree
    new_path = cmd.get("new_path") or ""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: _copy_entry(project_id, path, new_path),
    )
    payload = {"project_id": project_id, "path": path, "new_path": new_path}
    payload.update(result)
    await ws.send_text(json.dumps({
        "type": "project_file_copy_result",
        "data": payload,
    }, default=str))


async def handle_project_file_delete(ws, cmd: dict) -> None:
    project_id = (cmd.get("project_id") or "").strip()
    path = cmd.get("path") or ""  # no .strip() — see handle_project_file_tree
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: _delete_entry(project_id, path),
    )
    payload = {"project_id": project_id, "path": path}
    payload.update(result)
    await ws.send_text(json.dumps({
        "type": "project_file_delete_result",
        "data": payload,
    }, default=str))


async def handle_project_file_reveal(ws, cmd: dict) -> None:
    project_id = (cmd.get("project_id") or "").strip()
    path = cmd.get("path") or ""  # no .strip() — see handle_project_file_tree
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: _reveal_entry(project_id, path),
    )
    payload = {"project_id": project_id, "path": path}
    payload.update(result)
    await ws.send_text(json.dumps({
        "type": "project_file_reveal_result",
        "data": payload,
    }, default=str))


ACTIONS = {
    "project_file_tree": handle_project_file_tree,
    "project_file_search": handle_project_file_search,
    "project_file_read": handle_project_file_read,
    "project_file_write": handle_project_file_write,
    "project_file_create": handle_project_file_create,
    "project_file_rename": handle_project_file_rename,
    "project_file_copy": handle_project_file_copy,
    "project_file_delete": handle_project_file_delete,
    "project_file_reveal": handle_project_file_reveal,
}
