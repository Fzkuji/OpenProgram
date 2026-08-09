"""Project file search + read endpoints — backs the composer's @file mention.

Two endpoints:

* ``GET /api/file-search?q=...&root=...&limit=...`` — BFS walk under
  ``root`` (defaults to the running worker's cwd) returning files whose
  basename or relpath matches the needle, case-insensitively. Mirrors
  the TUI's ``cli/src/utils/fileCompletions.ts`` algorithm so both
  frontends present the same ranking.

* ``GET /api/file-read?path=...&root=...`` — read a single file as text.
  Used by the web composer to expand ``@path`` tokens into the outgoing
  message body, and by the chat's attachment viewer (which passes an
  ABSOLUTE ``path`` instead of a root-relative one). Limits payload size
  + blocks reads outside ``root`` so random users with a webui port open
  can't exfiltrate /etc/passwd.

* ``GET /api/file-raw?path=...`` — the BYTES behind an absolute path, so
  the chat can render an attachment the user or the agent sent. The
  files panel's ``/files/raw`` deliberately refuses absolute paths (it
  is scoped to one project id); rather than weaken that invariant, this
  is a second route with its own contract — an absolute path checked
  against ``attachments.readable_roots()``.

All are read-only and scoped. An explicit ``root`` is only accepted when
it falls under an allowed root (the project root or a session workdir) —
see ``_resolve_root`` — so the containment check isn't defeated by simply
asking for ``root=/etc``.
"""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse

from openprogram import attachments as _attach


# Same skiplist as cli/src/utils/fileCompletions.ts so web + tui rank
# identically. Hidden dotfolders are pruned separately (any name starting
# with ".").
_SKIP_DIRS: set[str] = {
    "node_modules", ".git", "dist", ".next", "__pycache__",
    ".venv", "venv", ".cache", "target", "build",
}

# Hard upper bound on a single file-read so a paste of a giant generated
# file doesn't blow the LLM context (and the WS frame).
_READ_MAX_BYTES = 256_000


def register(app) -> None:
    @app.get("/api/file-search")
    async def file_search(
        q: str = "",
        root: str | None = None,
        limit: int = 12,
        max_scan: int = 5000,
    ):
        """Return up to ``limit`` paths matching ``q`` under ``root``."""
        cwd = _resolve_root(root)
        matches = _walk(cwd, q, int(limit), int(max_scan))
        return JSONResponse(content={
            "root": str(cwd),
            "matches": matches,
        })

    @app.get("/api/file-read")
    async def file_read(path: str, root: str | None = None,
                        session_id: str = ""):
        """Read a single file as text.

        Two calling conventions: root-relative (the composer's ``@``
        expansion — refuses paths that escape ``root`` via ``..`` or
        symlinks resolving outside), or an absolute path checked against
        the attachment roots (the chat's attachment viewer).
        """
        if os.path.isabs(os.path.expanduser(path)):
            target = _attach.resolve_within(
                path, _attach.readable_roots(session_id or None))
            if target is None:
                raise HTTPException(status_code=403, detail="path not allowed")
            rel = str(target)
        else:
            cwd = _resolve_root(root)
            target = (cwd / path).resolve()
            try:
                # Path.is_relative_to is 3.9+; we're on 3.12. Catch typing
                # quirks where ``target`` is on a different drive on Win32.
                if not target.is_relative_to(cwd):
                    raise HTTPException(status_code=400,
                                        detail="path escapes root")
            except ValueError:
                raise HTTPException(status_code=400, detail="path escapes root")
            rel = str(target.relative_to(cwd))
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not a file")
        try:
            size = target.stat().st_size
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e))
        truncated = False
        try:
            with target.open("rb") as f:
                raw = f.read(_READ_MAX_BYTES + 1)
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e))
        if len(raw) > _READ_MAX_BYTES:
            raw = raw[:_READ_MAX_BYTES]
            truncated = True
        # Best-effort decode; fall back to a binary-safe replace. The
        # viewer needs to tell "text with odd bytes" from "not text at
        # all", so report it rather than showing a wall of U+FFFD.
        binary = b"\x00" in raw[:8192]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        return JSONResponse(content={
            "path": rel,
            "size": size,
            "truncated": truncated,
            "binary": binary,
            "content": text,
        })

    @app.get("/api/file-raw")
    async def file_raw(path: str, session_id: str = ""):
        """Raw bytes of an absolute path inside the attachment roots.

        Response headers mirror ``/files/raw``: untrusted bytes, so
        nosniff + a CSP sandbox everywhere, with the same PDF carve-out
        (a sandboxed response blocks the browser's built-in PDF viewer,
        and that viewer renders in its own process, not the page DOM).
        """
        target = _attach.resolve_within(
            path, _attach.readable_roots(session_id or None))
        if target is None:
            raise HTTPException(status_code=403, detail="path not allowed")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not a file")
        headers = {"X-Content-Type-Options": "nosniff",
                   "Content-Security-Policy": "sandbox"}
        guessed = mimetypes.guess_type(str(target))[0]
        if guessed and guessed.startswith("image/"):
            return FileResponse(target, media_type=guessed, headers=headers)
        if guessed == "application/pdf":
            return FileResponse(target, media_type=guessed,
                                headers={"X-Content-Type-Options": "nosniff"})
        return FileResponse(target, media_type="application/octet-stream",
                            filename=target.name, headers=headers)

    @app.get("/api/file-resolve")
    async def file_resolve(path: str, root: str | None = None):
        """Resolve a (possibly root-relative) path to an ABSOLUTE path +
        size, WITHOUT reading the content.

        Backs the composer's ``@``-mention path-reference: an attachment
        is delivered to the agent as a path it reads on demand (never
        inlined into the prompt). The agent's cwd is the session
        workdir, not the search root, so the mention must carry an
        absolute path. Escape-checked exactly like ``/api/file-read`` so
        a webui port can't be used to stat arbitrary files outside root.
        """
        cwd = _resolve_root(root)
        target = (cwd / path).resolve()
        try:
            if not target.is_relative_to(cwd):
                raise HTTPException(status_code=400,
                                    detail="path escapes root")
        except ValueError:
            raise HTTPException(status_code=400, detail="path escapes root")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not a file")
        try:
            size = target.stat().st_size
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e))
        return JSONResponse(content={"path": str(target), "size": size})


def _resolve_root(root: str | None) -> Path:
    """Return an absolute, existing directory.

    Empty / None ``root`` → :func:`_default_root`.

    An explicit ``root`` is NOT honoured as-is — it must resolve to (or
    inside) one of the allowed roots from :func:`_allowed_roots`.
    Otherwise the containment check in the routes below would be
    vacuous: an attacker passing ``?root=/etc&path=passwd`` would get a
    root they fully control, and every path is "inside" it.
    """
    if root:
        p = Path(os.path.expanduser(root)).resolve()
        allowed = _allowed_roots()
        if not any(p == a or p.is_relative_to(a) for a in allowed):
            raise HTTPException(status_code=400,
                                detail=f"root not allowed: {root}")
        if not p.is_dir():
            raise HTTPException(status_code=400,
                                detail=f"root not a directory: {root}")
        return p
    return _default_root()


def _allowed_roots() -> list[Path]:
    """Roots a caller may legitimately select via ``?root=``.

    The default project root plus the per-session workdirs (the
    composer passes a session workdir when expanding ``@`` mentions).
    """
    roots = [_default_root()]
    try:
        from openprogram.paths import get_state_dir
        sessions = Path(get_state_dir()).resolve() / "sessions"
        if sessions.is_dir():
            roots.append(sessions)
    except Exception:  # noqa: BLE001
        pass
    return roots


def _default_root() -> Path:
    """The project root — ``attachments.project_root()``. Shared with the
    attachment path policy so the two never drift apart."""
    return _attach.project_root()


def _walk(cwd: Path, needle: str, limit: int, max_scan: int) -> list[dict[str, Any]]:
    """BFS file walk. Mirrors fileCompletions.ts behaviour, lightly
    tuned: also accept exact substring match against the slash-joined
    relpath so deep matches like ``api/chat`` surface.
    """
    needle_l = needle.lower()
    out: list[dict[str, Any]] = []
    scanned = 0
    queue: list[Path] = [cwd]

    while queue and len(out) < limit and scanned < max_scan:
        directory = queue.pop(0)
        try:
            entries = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in entries:
            if name.startswith("."):
                continue
            if name in _SKIP_DIRS:
                continue
            full = directory / name
            try:
                is_dir = full.is_dir()
            except OSError:
                continue
            scanned += 1
            try:
                rel = full.relative_to(cwd)
            except ValueError:
                continue
            rel_str = str(rel)
            base_l = name.lower()
            rel_l = rel_str.lower()
            if (not needle_l
                    or needle_l in base_l
                    or needle_l in rel_l):
                out.append({"path": rel_str, "is_dir": is_dir})
                if len(out) >= limit:
                    break
            if is_dir:
                queue.append(full)
    return out
