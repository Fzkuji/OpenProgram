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

``path`` is always project-relative ("" = project root). Entries are
sorted dirs-first, then files, each alphabetically (case-insensitive);
dotfiles are included. Reads are capped at 1 MB (beyond → no content,
``too_large``) and binary files (NUL byte in the first 8 KiB) return
``binary`` instead of content.

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

# Hard cap on a single text read — the panel shows sources, not dumps.
_READ_MAX_BYTES = 1_000_000  # 1 MB
_BINARY_SNIFF_BYTES = 8192


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


ACTIONS = {
    "project_file_tree": handle_project_file_tree,
    "project_file_read": handle_project_file_read,
}
