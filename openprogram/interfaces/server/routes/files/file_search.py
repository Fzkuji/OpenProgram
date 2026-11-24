"""Project file search + read endpoints — backs the composer's @file mention.

Two endpoints:

* ``GET /api/file-search?q=...&root=...&limit=...`` — BFS walk under
  ``root`` (defaults to the running worker's cwd) returning files whose
  basename or relpath matches the needle, case-insensitively. Mirrors
  the TUI's ``apps/cli/src/utils/fileCompletions.ts`` algorithm so both
  frontends present the same ranking. With ``session_id`` (and, for a
  chat not created yet, ``project_id``) instead of ``root`` it searches
  the chat's working directory, the folder its agent runs in.

* ``GET /api/file-read?path=...&root=...`` — read a single file as text.
  Used by the web composer to expand ``@path`` tokens into the outgoing
  message body, and by the chat's attachment viewer (which passes an
  ABSOLUTE ``path`` instead of a root-relative one). Limits payload size
  + blocks reads outside ``root`` so random users with a webui port open
  can't exfiltrate /etc/passwd.

* ``GET /api/file-page?path=...`` — an HTML file as a sandboxed page
  (scripts on, opaque origin) for the chat card's thumbnail.

* ``POST /api/file-reveal?path=...`` — select that absolute path in the
  worker machine's file manager (same root check as file-raw).

* ``GET /api/file-paths?path=...&path=...`` — which of a reply's
  path-looking strings name a file the chat can open (stat only).

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

import asyncio
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from openprogram.state import attachments as _attach

_log = logging.getLogger(__name__)


# Same skiplist as apps/cli/src/utils/fileCompletions.ts so web + tui rank
# identically. Hidden dotfolders are pruned separately (any name starting
# with ".").
_SKIP_DIRS: set[str] = {
    "node_modules", ".git", "dist", ".next", "__pycache__",
    ".venv", "venv", ".cache", "target", "build",
}

# Hard upper bound on a single file-read so a paste of a giant generated
# file doesn't blow the LLM context (and the WS frame).
_READ_MAX_BYTES = 256_000

# Paths one ``/api/file-paths`` request may ask about: a reply's links and
# code spans, deduplicated by the client.
_PATHS_MAX = 64


def register(app) -> None:
    @app.get("/api/file-search")
    async def file_search(
        q: str = "",
        root: str | None = None,
        limit: int = 12,
        max_scan: int = 5000,
        session_id: str = "",
        project_id: str = "",
    ):
        """Return up to ``limit`` paths matching ``q`` under ``root``, or
        under the chat's working directory."""
        # A chat whose project folder is on another machine searches there.
        if not root:
            remote = await asyncio.to_thread(_remote_project, session_id, project_id)
            if remote is not None:
                matches = await asyncio.to_thread(_remote_matches, remote, q, int(limit), int(max_scan))
                return JSONResponse(content={"root": remote.root, "matches": matches})
        # Only finding the folder needs the conversation's lane; the walk
        # itself runs outside it, so typing an @-mention never queues a
        # Send or Stop of this chat behind a slow folder.
        cwd = await _off_loop(session_id, lambda: _resolve_root(root, session_id, project_id))
        matches = await asyncio.to_thread(_walk, cwd, q, int(limit), int(max_scan))
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
            target = _attach.resolve_session_attachment(
                path, session_id or None,
                _attach.readable_roots(session_id or None),
                allow_missing=True,
            )
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
        target = _attach.resolve_session_attachment(
            path, session_id or None,
            _attach.readable_roots(session_id or None),
            allow_missing=True,
        )
        if target is None:
            raise HTTPException(status_code=403, detail="path not allowed")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not a file")
        from .documents import disk_version
        headers = {"X-Content-Type-Options": "nosniff",
                   "Content-Security-Policy": "sandbox",
                   "Cache-Control": "no-store", "X-Document-Version": disk_version(target)}
        guessed = mimetypes.guess_type(str(target))[0]
        # Images, video and audio are shown inline in chat replies; their
        # real type lets <video>/<audio> play them, and Range requests let
        # the player seek without reading the whole file.
        if guessed and guessed.split("/")[0] in {"image", "video", "audio"}:
            return FileResponse(target, media_type=guessed, headers=headers)
        if guessed == "application/pdf":
            return FileResponse(target, media_type=guessed,
                                headers={key: value for key, value in headers.items() if key != "Content-Security-Policy"})
        return FileResponse(target, media_type="application/octet-stream",
                            filename=target.name, headers=headers)

    @app.get("/api/file-page")
    async def file_page(path: str, session_id: str = ""):
        """An HTML file as a page for the chat card's thumbnail frame.

        ``/api/file-raw`` serves HTML as an inert download, and a
        ``srcdoc`` frame inherits the app's ``script-src 'self'``, so a
        page drawn by its own scripts would show blank. Here the response
        carries ``sandbox allow-scripts`` without ``allow-same-origin``:
        the page runs with an opaque origin, so it cannot read the app's
        storage, and the owner check refuses its ``Origin: null``
        requests. Only .html / .htm files are served this way.
        """
        target = _attach.resolve_session_attachment(
            path, session_id or None,
            _attach.readable_roots(session_id or None),
            allow_missing=True,
        )
        if target is None:
            raise HTTPException(status_code=403, detail="path not allowed")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not a file")
        if target.suffix.lower() not in {".html", ".htm"}:
            raise HTTPException(status_code=415, detail="not an HTML file")
        return FileResponse(target, media_type="text/html", headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox allow-scripts",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        })

    @app.post("/api/file-reveal")
    async def file_reveal(path: str, session_id: str = ""):
        """Select an absolute path inside the attachment roots in the
        worker machine's file manager — the chat file card's "Reveal in
        Finder". Same root check as ``/api/file-raw``; it only shows the
        file, never opens or runs it."""
        target = _attach.resolve_session_attachment(
            path, session_id or None,
            _attach.readable_roots(session_id or None),
            allow_missing=True,
        )
        if target is None:
            raise HTTPException(status_code=403, detail="path not allowed")
        if not target.exists():
            raise HTTPException(status_code=404, detail="not found")
        from openprogram.interfaces.server.ws_actions.files.mutations import reveal_in_file_manager
        result = reveal_in_file_manager(str(target))
        if result.get("error"):
            raise HTTPException(status_code=500, detail=result["error"])
        return JSONResponse(content={"ok": True})

    @app.get("/api/file-paths")
    async def file_paths(path: list[str] = Query(default=[]), session_id: str = ""):
        """Which of these paths name a file the chat can open, and where.

        Backs the clickable file paths in a reply: the client sends every
        path-looking string of a reply in one request and links only the
        ones that come back. An absolute or ``~`` path is checked against
        the same roots as ``/api/file-raw``; a relative one is first joined
        to the chat's working directory. Stat only, never reads bytes;
        answers ``null`` for a path that is missing, a folder or outside
        every root, so the response never says which of those it was.
        """
        asked = list(dict.fromkeys(p for p in path if p))[:_PATHS_MAX]
        found = await _off_loop(session_id, lambda: _existing_files(asked, session_id))
        return JSONResponse(content={"files": found},
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/file-resolve")
    async def file_resolve(path: str, root: str | None = None,
                           session_id: str = "", project_id: str = ""):
        """Resolve a (possibly root-relative) path to an ABSOLUTE path +
        size, WITHOUT reading the content.

        Backs the composer's ``@``-mention path-reference: an attachment
        is delivered to the agent as a path it reads on demand (never
        inlined into the prompt). The agent's cwd is the session
        workdir, not the search root, so the mention must carry an
        absolute path. Escape-checked exactly like ``/api/file-read`` so
        a webui port can't be used to stat arbitrary files outside root.
        A ``session_id`` / ``project_id`` resolves the path in that chat's
        working directory, as ``/api/file-search`` found it.
        """
        def resolve() -> dict:
            cwd = _resolve_root(root, session_id, project_id)
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
            return {"path": str(target), "size": size}

        return JSONResponse(content=await _off_loop(session_id, resolve))


async def _off_loop(session_id: str, fn):
    """Run ``fn`` in a worker thread: a chat's working directory is read
    under its conversation lock, which a turn may hold, and a folder walk
    is slow on its own, so neither may stall the event loop. A named chat
    waits in that conversation's lane, as every conversation read does."""
    if session_id:
        from openprogram.state.store.session.conversation_work import run_for_conversation
        return await run_for_conversation(session_id, fn)
    return await asyncio.to_thread(fn)


def _existing_files(paths: list[str], session_id: str) -> dict[str, str | None]:
    """``/api/file-paths``: each asked path mapped to the absolute file it
    names inside the readable roots, or ``None``."""
    session = session_id or None
    roots = _attach.readable_roots(session)
    chat_root = _chat_root(session_id, "") if session_id else None
    found: dict[str, str | None] = {}
    for asked in paths:
        found[asked] = None
        if "\0" in asked or len(asked) > 1024:
            continue
        if asked.startswith("~") or os.path.isabs(asked):
            candidate = asked
        elif chat_root is not None:
            candidate = str(chat_root / asked)
        else:
            continue
        try:
            target = _attach.resolve_session_attachment(candidate, session, roots, allow_missing=True)
            if target is not None and target.is_file():
                found[asked] = str(target)
        except (OSError, ValueError):
            continue
    return found


def _remote_project(session_id: str, project_id: str):
    """The chat's project when its folder is on a machine that keeps its own files, else None."""
    try:
        from openprogram.interfaces.server.ws_actions.files import remote as _remote
        from openprogram.state.store.project.project_store import project_for_session
        if not project_id and session_id:
            project = project_for_session(session_id)
            project_id = project.id if project is not None else ""
        return _remote.target(project_id) if project_id else None
    except Exception:  # noqa: BLE001 - an unknown project searches this computer as before
        _log.debug("remote project lookup failed", exc_info=True)
        return None


def _remote_matches(remote, needle: str, limit: int, max_scan: int) -> list[dict[str, Any]]:
    """``_walk`` on the project's machine: one listing there, hidden entries left out, matched here."""
    try:
        found, _basis = remote_search(remote, "", max(limit, max_scan))
    except Exception:  # noqa: BLE001 - an unreachable machine has no matches
        return []
    needle_l = needle.lower()
    out: list[dict[str, Any]] = []
    for kind, _size, _mtime, rel in sorted(found, key=lambda row: (row[3].count("/"), row[3])):
        if any(part.startswith(".") or part in _SKIP_DIRS for part in rel.split("/")):
            continue
        if not needle_l or needle_l in rel.rsplit("/", 1)[-1].lower() or needle_l in rel.lower():
            out.append({"path": rel, "is_dir": kind == "dir"})
            if len(out) >= limit:
                break
    return out


def remote_search(remote, path: str, limit: int):
    from openprogram.interfaces.server.ws_actions.files import remote as _remote
    from openprogram.interfaces.server.ws_actions.files.query import _QueryLimitError
    try:
        return _remote.search(remote, path, limit)
    except _QueryLimitError:
        return [], ()


def _resolve_root(root: str | None, session_id: str = "",
                  project_id: str = "") -> Path:
    """Return an absolute, existing directory.

    Empty / None ``root`` → the chat's working directory
    (:func:`_chat_root`) when a chat is named, else :func:`_default_root`.

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
    return _chat_root(session_id, project_id) or _default_root()


def _chat_root(session_id: str, project_id: str) -> Path | None:
    """The directory a chat's agent works in, where its ``@`` mentions
    resolve: the picked project's folder for a chat not created yet,
    else the session's worktree or project folder (the default project's
    for an unbound chat), else its own ``workdir/``. ``None`` when the
    request names no chat or nothing resolves."""
    candidates: list[Path] = []
    if project_id:
        try:
            from openprogram.state.store.project.project_store import get_project
            proj = get_project(project_id)
            if proj is not None and proj.path:
                candidates.append(Path(proj.path).expanduser())
        except Exception:  # noqa: BLE001
            # Unresolvable project: fall through to the session's own roots.
            _log.debug("project %s path unresolved", project_id, exc_info=True)
    if session_id and "/" not in session_id and "\\" not in session_id:
        try:
            from openprogram.runtime.conversation.session_settings.workdir import (
                runtime_location_for, session_workdir_for,
            )
            workdir = runtime_location_for(session_id, use_context=False).get("workdir")
            if isinstance(workdir, str) and workdir:
                candidates.append(Path(workdir).expanduser())
            own = session_workdir_for(session_id)
            if own is not None:
                candidates.append(Path(own))
        except Exception:  # noqa: BLE001
            # Unresolvable workdir: the search falls back to other candidates.
            _log.debug("workdir unresolved for session %s", session_id, exc_info=True)
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_dir():
            return resolved
    return None


def _allowed_roots() -> list[Path]:
    """Roots a caller may legitimately select via ``?root=``.

    The default project root plus the per-session workdirs (the
    composer passes a session workdir when expanding ``@`` mentions).
    """
    roots = [_default_root()]
    try:
        from openprogram.state.paths import get_state_dir
        projects = Path(get_state_dir()).resolve() / "projects"
        roots.extend(sorted(d for d in projects.glob("*/chats") if d.is_dir()))
    except Exception:  # noqa: BLE001
        # Session workdirs only widen the allowed set; the default root stays.
        _log.debug("session workdir roots not listed", exc_info=True)
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
