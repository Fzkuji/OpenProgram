"""Workdir / folder picker endpoints + history / canvas readers.

Filesystem-side endpoints used by the UI for picking working directories,
listing prior conversation history, and reading the canvas markdown file.
"""
from __future__ import annotations

import json
import os
import sys

from fastapi.responses import JSONResponse


def register(app):
    @app.post("/api/pick-folder")
    async def pick_folder(body: dict = None):
        """Open the OS-native folder chooser. macOS only."""
        import pathlib
        import subprocess
        if sys.platform != "darwin":
            return JSONResponse(
                content={"error": "native folder picker only supported on macOS"},
                status_code=501,
            )
        start = (body or {}).get("start") or str(pathlib.Path.home())
        start = os.path.abspath(os.path.expanduser(start))
        if not os.path.isdir(start):
            start = str(pathlib.Path.home())
        script = (
            f'POSIX path of (choose folder with prompt '
            f'"Select working directory" default location '
            f'POSIX file "{start}")'
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=600,
            )
        except Exception as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=500)
        if result.returncode != 0:
            return JSONResponse(content={"path": None})
        path = result.stdout.strip().rstrip("/")
        return JSONResponse(content={"path": path or None})

    @app.get("/api/browse")
    async def browse_directory(path: str = None):
        """List subdirectories of a path for the workdir picker."""
        import pathlib
        home = str(pathlib.Path.home())
        target = path or home
        target = os.path.abspath(os.path.expanduser(target))
        if not os.path.isdir(target):
            target = home
        try:
            entries = sorted(os.listdir(target))
        except PermissionError:
            return JSONResponse(
                content={"error": f"Permission denied: {target}"},
                status_code=403,
            )
        subdirs = []
        for name in entries:
            if name.startswith("."):
                continue
            full = os.path.join(target, name)
            if os.path.isdir(full):
                subdirs.append({"name": name, "path": full})
        parent = os.path.dirname(target) if target != "/" else None
        return JSONResponse(content={
            "path": target,
            "parent": parent if parent and parent != target else None,
            "subdirs": subdirs,
            "home": home,
        })

    @app.get("/api/workdir/defaults")
    async def workdir_defaults(session_id: str = None, function_name: str = None):
        import pathlib
        from openprogram.webui import server as _s
        repo_root = os.path.abspath(os.path.join(
            os.path.dirname(_s.__file__), "..", ".."
        ))
        last = None
        if session_id and function_name:
            with _s._sessions_lock:
                conv = _s._sessions.get(session_id)
                if conv:
                    last = conv.get("last_workdirs", {}).get(function_name)
        return JSONResponse(content={
            "last": last,
            "repo": repo_root,
            "home": str(pathlib.Path.home()),
        })

    @app.get("/api/history")
    async def get_history():
        from openprogram.webui import server as _s
        with _s._sessions_lock:
            history = [
                {"id": c["id"], "title": c["title"], "created_at": c["created_at"],
                 "messages": c.get("messages", []),
                 "message_count": len(c.get("messages", []))}
                for c in sorted(_s._sessions.values(), key=lambda c: c["created_at"], reverse=True)
            ]
        return JSONResponse(content=history)

    @app.post("/api/history")
    async def save_history(body: dict = None):
        from openprogram.webui import server as _s
        if body and "session_id" in body:
            session_id = body["session_id"]
            with _s._sessions_lock:
                if session_id in _s._sessions:
                    return JSONResponse(content={"saved": True})
        return JSONResponse(content={"saved": False})

    @app.get("/api/canvas")
    async def get_canvas(path: str = None):
        """Return the current canvas.md content + path + mtime."""
        import os as _os
        from openprogram.tools.canvas.canvas import _resolve_path, _BLOCK_RE
        resolved = _resolve_path(path)
        try:
            st = _os.stat(resolved)
            mtime = int(st.st_mtime * 1000)
            with open(resolved, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            return JSONResponse(content={
                "path": resolved, "content": "", "mtime": 0,
                "blocks": [], "exists": False,
            })
        blocks = [
            {"id": m.group("id"), "length": len(m.group("body"))}
            for m in _BLOCK_RE.finditer(content)
        ]
        return JSONResponse(content={
            "path": resolved,
            "content": content,
            "mtime": mtime,
            "blocks": blocks,
            "exists": True,
        })
