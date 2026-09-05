"""Filesystem mutation primitives for canonical project-file actions."""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys

from .files_shared import (
    _BINARY_SNIFF_BYTES, _READ_MAX_BYTES, _WRITE_MAX_BYTES, _file_digest, _open,
)
from .files_query import _resolve

def _read_file(project_id: str, path: str) -> dict:
    target, error = _resolve(project_id, path)
    if error:
        return {"error": error}
    if not os.path.isfile(target):
        return {"error": f"not a file: {path!r}"}
    try:
        stat = os.stat(target)
        result: dict = {"size": stat.st_size, "mtime": stat.st_mtime}
        with _open(target, "rb") as f:
            head = f.read(_BINARY_SNIFF_BYTES)
            if b"\x00" in head:
                result["binary"] = True
                return result
            if stat.st_size > _READ_MAX_BYTES:
                result["too_large"] = True
                return result
            # Read at most limit+1 bytes after the initial stat. The file can
            # grow between stat() and read(); an unbounded read would turn
            # that TOCTOU window into an allocation proportional to growth.
            remaining = _READ_MAX_BYTES + 1 - len(head)
            raw = head + f.read(max(0, remaining))
            if len(raw) > _READ_MAX_BYTES:
                result["too_large"] = True
                return result
    except OSError as e:
        return {"error": f"{type(e).__name__}: {e}"}
    result["content"] = raw.decode("utf-8", errors="replace")
    result["revision"] = hashlib.sha256(raw).hexdigest()
    return result


def _write_file(project_id: str, path: str, content: str,
                expected_mtime: float | None,
                expected_revision: str | None = None) -> dict:
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
    if expected_revision is not None:
        # mtime can be restored by another writer or have insufficient
        # resolution. The content digest is the durable baseline identity.
        if _file_digest(target) != expected_revision:
            return {"conflict": True}
    try:
        # 原子替换：先写同目录临时文件再 os.replace——中途崩溃/磁盘满
        # 不会留下截断的目标文件。
        tmp = f"{target}.tmp.{os.getpid()}"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, target)
        return {
            "ok": True,
            "mtime": os.stat(target).st_mtime,
            "revision": hashlib.sha256(raw).hexdigest(),
        }
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
            with _open(target, "x"):
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
    src_base = os.path.basename(src)
    requested_base = os.path.basename(new_path.replace("/", os.sep))
    case_only = (
        src_base != requested_base
        and src_base.lower() == requested_base.lower()
        and os.path.exists(dst)
        and os.path.samefile(src, dst)
    )
    if os.path.exists(dst) and not case_only:
        return {"error": f"destination already exists: {new_path!r}"}
    try:
        if case_only:
            dst = os.path.join(os.path.dirname(dst), requested_base)
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
