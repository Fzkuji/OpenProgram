"""Project files panel WS actions — browse + read files under a project root.

Wire format::

    in:  {"action": "project_file_tree", "project_id": "...", "path": ""}
    out: {"type": "project_file_tree_result",
          "data": {"project_id", "path",
                   "entries": [{"name", "type": "file"|"dir", "size", "mtime"}],
                   "error"?}}

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

``path`` is always project-relative ("" = project root). Entries are
sorted dirs-first, then files, each alphabetically (case-insensitive);
dotfiles are included. Reads are capped at 1 MB (beyond → no content,
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
import shutil
import subprocess
import sys

# Hard cap on a single text read — the panel shows sources, not dumps.
_READ_MAX_BYTES = 1_000_000  # 1 MB
_BINARY_SNIFF_BYTES = 8192
# Writes come from the in-browser editor; 5 MB is far past any file a
# human edits in a textarea.
_WRITE_MAX_BYTES = 5_000_000  # 5 MB


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
    from openprogram.store import project_store as _projects
    proj = _projects.get_project(project_id)
    if proj is None or not proj.path:
        return None, f"unknown project {project_id!r}"
    root = os.path.realpath(os.path.expanduser(proj.path))
    target = os.path.realpath(os.path.join(root, path))
    if target != root and not target.startswith(root + os.sep):
        return None, "path escapes project root"
    return target, None


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
    project_id = (cmd.get("project_id") or "").strip()
    # No .strip(): filenames with leading/trailing whitespace must
    # round-trip so the echoed ``path`` matches the request.
    path = cmd.get("path") or ""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: _list_tree(project_id, path),
    )
    payload = {"project_id": project_id, "path": path,
               "entries": result.get("entries") or []}
    if result.get("error"):
        payload["error"] = result["error"]
    await ws.send_text(json.dumps({
        "type": "project_file_tree_result",
        "data": payload,
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
    "project_file_read": handle_project_file_read,
    "project_file_write": handle_project_file_write,
    "project_file_create": handle_project_file_create,
    "project_file_rename": handle_project_file_rename,
    "project_file_copy": handle_project_file_copy,
    "project_file_delete": handle_project_file_delete,
    "project_file_reveal": handle_project_file_reveal,
}
