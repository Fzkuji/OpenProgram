"""Project file search + read endpoints — backs the composer's @file mention.

Two endpoints:

* ``GET /api/file-search?q=...&root=...&limit=...`` — BFS walk under
  ``root`` (defaults to the running worker's cwd) returning files whose
  basename or relpath matches the needle, case-insensitively. Mirrors
  the TUI's ``cli/src/utils/fileCompletions.ts`` algorithm so both
  frontends present the same ranking.

* ``GET /api/file-read?path=...&root=...`` — read a single file as text.
  Used by the web composer to expand ``@path`` tokens into the outgoing
  message body. Limits payload size + blocks reads outside ``root`` so
  random users with a webui port open can't exfiltrate /etc/passwd.

Both are read-only and scoped to the requested root (defaults to
``os.getcwd()``). The composer passes whatever workdir was picked for
the active session so search ranges match what the agent would see.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse


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
    async def file_read(path: str, root: str | None = None):
        """Read a single file under ``root``. Refuses paths that escape
        ``root`` via ``..`` or symlinks resolving outside.
        """
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
        # Best-effort decode; fall back to a binary-safe replace.
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        return JSONResponse(content={
            "path": str(target.relative_to(cwd)),
            "size": size,
            "truncated": truncated,
            "content": text,
        })


def _resolve_root(root: str | None) -> Path:
    """Return an absolute, existing directory.

    Lookup order when ``root`` is empty / None:

      1. ``OPENPROGRAM_PROJECT_ROOT`` env var (deployment override).
      2. The directory containing ``openprogram/`` — i.e. the package
         parent. Works when the user launched ``openprogram worker run``
         from the project root, which is the common case.
      3. Process cwd.

    Explicit ``root`` is honoured as-is (still resolved + existence-
    checked) so the composer can pass per-session workdirs once we add
    that wiring.
    """
    if root:
        p = Path(os.path.expanduser(root)).resolve()
        if not p.is_dir():
            raise HTTPException(status_code=400,
                                detail=f"root not a directory: {root}")
        return p
    env = os.environ.get("OPENPROGRAM_PROJECT_ROOT")
    if env:
        p = Path(os.path.expanduser(env)).resolve()
        if p.is_dir():
            return p
    try:
        import openprogram
        pkg_dir = Path(openprogram.__file__).resolve().parent
        parent = pkg_dir.parent
        if parent.is_dir():
            return parent
    except Exception:  # noqa: BLE001
        pass
    return Path(os.getcwd()).resolve()


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
