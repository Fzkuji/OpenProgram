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
                expected_revision: str | None = None,
                idempotency_key: str | None = None,
                editor_id: str = "manual") -> dict:
    target, error = _resolve(project_id, path)
    if error:
        return {"error": error}
    raw = content.encode("utf-8")
    if len(raw) > _WRITE_MAX_BYTES:
        return {"error": "content exceeds 5 MB"}
    from openprogram.store.document_history import DocumentHistory, DocumentHistoryError
    try:
        result = DocumentHistory().publish(
            project_id, path, raw, baseline_revision=expected_revision,
            expected_mtime=expected_mtime, idempotency_key=idempotency_key,
            editor_id=editor_id,
        )
        if result.get("status") == "committed":
            return {**result, "ok": True, "status": "ready"}
        return result
    except DocumentHistoryError as exc:
        if exc.code == "CONFLICT":
            return {"conflict": True, "error_code": "CONFLICT"}
        return {"error": str(exc), "error_code": exc.code}
    except (OSError, RuntimeError, ValueError):
        return {"status": "recovery_required", "error_code": "RECOVERY_REQUIRED",
                "error": "document publication could not be confirmed"}


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
