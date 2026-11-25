"""Project files on a machine that keeps its own files (an SSH host), through its shell.

A project is a folder on one machine. When that machine's shell keeps
files there (``Shell.remote_files`` in ``host.backend``), the Files panel reads and changes the
folder on the machine: every operation here is one shell command through
the machine's backend (``host.machines.remote.RemoteFS``), never a look at
this computer's disk. The same answers come back as for a local folder, so
the panel does not tell the two apart.

Paths stay inside the project folder: a relative path is checked before it
is joined to the folder (no absolute path, no ``..``, no NUL).
"""
from __future__ import annotations

import hashlib
import posixpath
import secrets
import shlex
import time
from typing import Optional

from . import shared as _shared

_IGNORED = sorted(_shared._SEARCH_IGNORED_DIRS)


class Remote:
    """One remote project's folder and the machine it is on."""

    def __init__(self, project, fs) -> None:
        self.project = project
        self.fs = fs
        self.root = project.path.rstrip("/") or "/"

    def full(self, relative: str) -> str:
        return self.root if not relative else f"{self.root.rstrip('/')}/{relative}"

    def shell(self, command: str, timeout: float = 60.0) -> tuple[int, str, str]:
        return self.fs.shell(command, timeout=timeout)


def target(project_id: object) -> Optional[Remote]:
    """The remote project for ``project_id``, or None when its folder is on this computer's disk.

    A project whose machine was removed or switched off still answers here:
    every operation on it then reports why, and nothing touches this computer.
    """
    if not isinstance(project_id, str) or not project_id:
        return None
    from openprogram.state.store.project import project_store as projects
    project = projects.get_project(project_id)
    if project is None or not project.path or projects.folder_is_here(project):
        return None
    from openprogram.host.machines import MachineError
    from openprogram.host.machines.remote import RefusedFS, RemoteFS
    try:
        fs = RemoteFS(projects.project_machine(project))
    except MachineError as exc:
        fs = RefusedFS(str(exc))
    return Remote(project, fs)


def relative(path: object) -> tuple[Optional[str], Optional[str]]:
    """A canonical project-relative path, or an error."""
    if not isinstance(path, str) or "\x00" in path or path.startswith(("/", "\\")):
        return None, "path escapes project root"
    normalized = posixpath.normpath(path.replace("\\", "/") or "")
    if normalized in ("", "."):
        return "", None
    if normalized == ".." or normalized.startswith("../"):
        return None, "path escapes project root"
    return normalized, None


def _q(path: str) -> str:
    return shlex.quote(path)


# ---- listing ------------------------------------------------------------------

# GNU find prints the type, size, time and name of each entry in one pass; a
# machine without it (BSD) is listed with ``ls`` and types read from the
# trailing slash, without sizes or times.
_LIST = ("if find . -maxdepth 0 -printf '' 2>/dev/null; then "
         "find . -mindepth 1 -maxdepth 1 -printf '%y\\t%s\\t%T@\\t%P\\n' 2>/dev/null; "
         "else echo '#ls'; ls -1Ap; fi")


def _rows(text: str) -> list[tuple[str, int, float, str]]:
    """``(kind, size, mtime, name)`` from a listing's output."""
    lines = text.splitlines()
    rows: list[tuple[str, int, float, str]] = []
    if lines and lines[0] == "#ls":
        for line in lines[1:]:
            if not line or line in ("./", "../"):
                continue
            is_dir = line.endswith("/")
            rows.append(("dir" if is_dir else "file", 0, 0.0, line.rstrip("/")))
        return rows
    for line in lines:
        parts = line.split("\t", 3)
        if len(parts) != 4 or not parts[3]:
            continue
        kind = "dir" if parts[0] == "d" else "file"
        try:
            size, mtime = int(parts[1]), float(parts[2])
        except ValueError:
            size, mtime = 0, 0.0
        rows.append((kind, size, mtime, parts[3]))
    return rows


def directory(remote: Remote, path: str) -> tuple[list[dict], tuple]:
    """``(entries, basis)`` of one folder, as ``query._directory_snapshot_fd`` gives them.

    Raises FileNotFoundError when the folder is not there and
    ConnectionError when the machine cannot be reached.
    """
    code, out, err = remote.shell(f"cd -- {_q(remote.full(path))} && {{ {_LIST}; }}")
    if code != 0:
        raise FileNotFoundError(err.strip() or f"not a folder: {path!r}")
    rows = _rows(out)
    entries = [{"name": name, "type": kind, "size": size, "mtime": mtime} for kind, size, mtime, name in rows]
    entries.sort(key=lambda row: (0 if row["type"] == "dir" else 1, row["name"].casefold(), row["name"]))
    basis = tuple(sorted(((name, kind, size, mtime) for kind, size, mtime, name in rows),
                         key=lambda row: (row[0].casefold(), row[0])))
    return entries, basis


def ignored(remote: Remote, paths: list[str]) -> set[str]:
    """The paths git ignores in the project folder, asked on the machine."""
    if not paths:
        return set()
    data = ("\0".join(paths) + "\0").encode()
    code, out, err = remote.fs._run(f"git -C {_q(remote.root)} check-ignore -z --stdin", data=data, timeout=20)
    if code not in (0, 1, 128):
        raise OSError(err.decode("utf-8", "replace").strip() or "Git ignore query failed")
    return set(out.decode("utf-8", "replace").split("\0"))


def search(remote: Remote, path: str, limit: int) -> tuple[list[tuple[str, int, float, str]], tuple]:
    """Every entry under ``path`` (ignored folders pruned) as ``(kind, size, mtime, relative)``."""
    prune = " -o ".join(f"-name {_q(name)}" for name in _IGNORED)
    gnu = (f"find . \\( {prune} \\) -prune -o -mindepth 1 -printf '%y\\t%s\\t%T@\\t%P\\n' 2>/dev/null "
           f"| head -n {int(limit) + 1}")
    bsd = (f"echo '#find'; find . \\( {prune} \\) -prune -o -mindepth 1 -type d -print 2>/dev/null "
           f"| awk '{{print \"d\\t\" substr($0, 3)}}' | head -n {int(limit) + 1}; "
           f"find . \\( {prune} \\) -prune -o -mindepth 1 -type f -print 2>/dev/null "
           f"| awk '{{print \"f\\t\" substr($0, 3)}}' | head -n {int(limit) + 1}")
    code, out, err = remote.shell(
        f"cd -- {_q(remote.full(path))} && if find . -maxdepth 0 -printf '' 2>/dev/null; "
        f"then {gnu}; else {bsd}; fi", timeout=60)
    if code != 0:
        raise FileNotFoundError(err.strip() or f"not a folder: {path!r}")
    lines = out.splitlines()
    rows: list[tuple[str, int, float, str]] = []
    if lines and lines[0] == "#find":
        for line in lines[1:]:
            kind, _, name = line.partition("\t")
            if name:
                rows.append(("dir" if kind == "d" else "file", 0, 0.0, name))
    else:
        rows = _rows(out)
    if len(rows) > limit:
        from .query import _QueryLimitError
        raise _QueryLimitError
    rel = [(kind, size, mtime, f"{path}/{name}" if path else name) for kind, size, mtime, name in rows]
    basis = tuple(sorted((name, kind, size, mtime) for kind, size, mtime, name in rel))
    return rel, basis


# ---- files ----------------------------------------------------------------------


def read_file(remote: Remote, path: str) -> dict:
    rel, error = relative(path)
    if error:
        return {"error": error}
    full = remote.full(rel)
    try:
        info = remote.fs.stat(full)
        if not info.exists or info.is_dir:
            return {"error": f"not a file: {path!r}"}
        result: dict = {"size": info.size, "mtime": _mtime(remote, full)}
        if info.size > _shared._READ_MAX_BYTES:
            code, head, _ = remote.fs._run(f"head -c {_shared._BINARY_SNIFF_BYTES} -- {_q(full)}")
            if b"\x00" in head:
                result["binary"] = True
            else:
                result["too_large"] = True
            return result
        raw = remote.fs.read_bytes(full)
    except (OSError, ConnectionError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    if b"\x00" in raw[:_shared._BINARY_SNIFF_BYTES]:
        result["binary"] = True
        return result
    result["content"] = raw.decode("utf-8", errors="replace")
    result["revision"] = hashlib.sha256(raw).hexdigest()
    return result


def _mtime(remote: Remote, full: str) -> Optional[float]:
    code, out, _ = remote.shell(f"stat -c %Y -- {_q(full)} 2>/dev/null || stat -f %m -- {_q(full)}", timeout=20)
    try:
        return float(out.strip().splitlines()[-1]) if code == 0 and out.strip() else None
    except ValueError:
        return None


def write_file(remote: Remote, path: str, content: str, expected_revision: Optional[str]) -> dict:
    """Replace a file's content when it still is what the editor read (``expected_revision``)."""
    rel, error = relative(path)
    if error or not rel:
        return {"error": error or "path is required"}
    raw = content.encode("utf-8")
    if len(raw) > _shared._WRITE_MAX_BYTES:
        return {"error": "content exceeds 5 MB"}
    full = remote.full(rel)
    try:
        info = remote.fs.stat(full)
        if info.is_dir:
            return {"error": f"not a file: {path!r}"}
        if expected_revision is not None:
            current = hashlib.sha256(remote.fs.read_bytes(full)).hexdigest() if info.exists else None
            if current != expected_revision:
                return {"conflict": True, "error_code": "CONFLICT"}
        remote.fs.write_bytes(full, raw)
    except (OSError, ConnectionError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    revision = hashlib.sha256(raw).hexdigest()
    return {"ok": True, "status": "ready", "revision": revision, "after_revision": revision}


def create_entry(remote: Remote, path: str, kind: str) -> dict:
    if kind not in ("file", "dir"):
        return {"error": "kind must be 'file' or 'dir'"}
    rel, error = relative(path)
    if error or not rel:
        return {"error": error or "path is required"}
    full = remote.full(rel)
    parent = posixpath.dirname(full)
    make = f"mkdir -- {_q(full)}" if kind == "dir" else f"( set -C; : > {_q(full)} )"
    return _change(remote, f"[ -d {_q(parent)} ] || {{ echo {_q('parent directory does not exist for ' + rel)} >&2; exit 3; }}; "
                           f"[ ! -e {_q(full)} ] || {{ echo {_q('already exists: ' + rel)} >&2; exit 4; }}; {make}")


def rename_entry(remote: Remote, path: str, new_path: str) -> dict:
    return _move(remote, path, new_path, "mv")


def copy_entry(remote: Remote, path: str, new_path: str) -> dict:
    return _move(remote, path, new_path, "cp -RP")


def _move(remote: Remote, path: str, new_path: str, verb: str) -> dict:
    src, error = relative(path)
    if error or not src:
        return {"error": error or "path is required"}
    dst, error = relative(new_path)
    if error or not dst:
        return {"error": error or "new_path is required"}
    a, b = remote.full(src), remote.full(dst)
    return _change(remote, f"[ -e {_q(a)} ] || [ -L {_q(a)} ] || {{ echo {_q('source does not exist: ' + src)} >&2; exit 3; }}; "
                           f"[ ! -e {_q(b)} ] || {{ echo {_q('destination already exists: ' + dst)} >&2; exit 4; }}; "
                           f"{verb} -- {_q(a)} {_q(b)}")


def delete_entry(remote: Remote, path: str) -> dict:
    """Move an entry into the machine's own OpenProgram trash, so it can be got back there."""
    rel, error = relative(path)
    if error:
        return {"error": error}
    if not rel:
        return {"error": "refusing to delete project root"}
    full = remote.full(rel)
    entry = f"{int(time.time())}-{secrets.token_hex(4)}"
    trash = f"$HOME/.openprogram/trash/{entry}"
    return _change(remote, f"[ -e {_q(full)} ] || [ -L {_q(full)} ] || {{ echo {_q('does not exist: ' + rel)} >&2; exit 3; }}; "
                           f"mkdir -p \"{trash}\" && mv -- {_q(full)} \"{trash}/\"")


def _change(remote: Remote, command: str) -> dict:
    try:
        code, _, err = remote.shell(command)
    except ConnectionError as exc:
        return {"error": f"ConnectionError: {exc}"}
    return {"ok": True} if code == 0 else {"error": err.strip() or f"exited {code}"}


# ---- metadata ---------------------------------------------------------------------


def file_info(remote: Remote, path: str) -> dict:
    rel, error = relative(path)
    if error:
        return {"error_code": "INVALID_REQUEST", "error": error, "status": "error"}
    full = remote.full(rel)
    try:
        code, out, err = remote.shell(
            f"if [ -L {_q(full)} ]; then echo l; elif [ -d {_q(full)} ]; then echo d; "
            f"elif [ -e {_q(full)} ]; then echo f; else echo n; fi; "
            f"stat -c '%s %Y %A' -- {_q(full)} 2>/dev/null || stat -f '%z %m %Sp' -- {_q(full)} 2>/dev/null",
            timeout=20)
    except ConnectionError as exc:
        return {"error_code": "IO_ERROR", "error": str(exc), "status": "error"}
    lines = out.split("\n")
    kind = {"l": "symlink", "d": "dir", "f": "file"}.get(lines[0].strip() if lines else "n")
    if kind is None:
        return {"error_code": "NOT_FOUND", "error": f"does not exist: {path!r}", "status": "error"}
    parts = lines[1].split() if len(lines) > 1 else []
    size = int(parts[0]) if parts and parts[0].isdigit() else None
    mtime = float(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    return {"status": "ready", "type": kind, "name": posixpath.basename(full) or full,
            "absolute_path": full, "size": size if kind != "dir" else None, "mtime": mtime,
            "created_at": None, "permissions": parts[2] if len(parts) > 2 else None,
            "revision": [0, 0, int((mtime or 0) * 1_000_000_000)]}


def folder_size(remote: Remote, path: str, operation: str) -> dict:
    """A folder's size in one ``du`` on the machine; ``peek`` answers from nothing kept."""
    if operation == "peek":
        return {"state": "unknown", "bytes": None}
    if operation in ("continue", "cancel"):
        return {"state": "cancelled", "bytes": None, "entries": 0, "skipped": 0}
    rel, error = relative(path)
    if error:
        return {"error_code": "INVALID_REQUEST", "error": error, "status": "error"}
    try:
        code, out, err = remote.shell(f"du -sk -- {_q(remote.full(rel))} 2>/dev/null", timeout=60)
    except ConnectionError as exc:
        return {"error_code": "IO_ERROR", "error": str(exc), "status": "error"}
    first = out.split()[0] if out.split() else ""
    if not first.isdigit():
        return {"error_code": "IO_ERROR", "error": err.strip() or "size unavailable", "status": "error"}
    return {"state": "complete", "bytes": int(first) * 1024, "entries": 0, "skipped": 0, "complete": True,
            "token": None, "updated_at": time.time(), "revision": [0, 0, 0]}


__all__ = ["Remote", "copy_entry", "create_entry", "delete_entry", "directory", "file_info", "folder_size",
           "ignored", "read_file", "relative", "rename_entry", "search", "target", "write_file"]
