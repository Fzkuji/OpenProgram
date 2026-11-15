"""Archive listing and member bytes — zip and tar files read in place.

A chat reply's zip card shows the archive's first level, and opening it
shows the whole tree with each previewable member readable. Nothing is
extracted to disk: the listing comes from the archive's own index (zip
central directory, tar headers) and a member's bytes are streamed into
memory up to :data:`MEMBER_MAX_BYTES`.

* ``GET /api/archive-list`` — ``{format, entries, total, truncated}``;
  each entry is ``{path, size, dir}`` with ``/``-separated paths.
* ``GET /api/archive-member`` — one member's bytes, with the same
  untrusted-bytes headers as ``/api/file-raw``.

The archive itself is addressed the two ways the viewer already uses:
an absolute path checked against the attachment roots (chat files), or
a project id plus a project-relative path (the files panel). Member
names are only looked up in the archive's index, never joined onto a
filesystem path, so a ``../`` name cannot reach anything.
"""
from __future__ import annotations

import mimetypes
import os
import tarfile
import zipfile
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response

from openprogram.state import attachments as _attach

#: Entries returned by one listing; a larger archive says it was cut.
LIST_MAX_ENTRIES = 5000
#: Largest archive file read at all (a compressed tar is read in full).
ARCHIVE_MAX_BYTES = 512 * 1024 * 1024
#: Largest member handed to the viewer.
MEMBER_MAX_BYTES = 32 * 1024 * 1024

_TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")


def archive_format(name: str) -> str | None:
    """``"zip"`` / ``"tar"`` by file name, else None."""
    lower = name.lower()
    if lower.endswith(".zip"):
        return "zip"
    if lower.endswith(_TAR_SUFFIXES):
        return "tar"
    return None


def _target(path: str, session_id: str, project_id: str) -> Path:
    if project_id:
        from openprogram.interfaces.server.ws_actions.files import _resolve
        resolved, error = _resolve(project_id, path)
        if resolved is None:
            raise HTTPException(status_code=403 if error == "path escapes project root" else 404,
                                detail=error or "not found")
        target = Path(resolved)
    else:
        found = _attach.resolve_session_attachment(
            path, session_id or None, _attach.readable_roots(session_id or None),
            allow_missing=True,
        )
        if found is None:
            raise HTTPException(status_code=403, detail="path not allowed")
        target = found
    if not target.is_file():
        raise HTTPException(status_code=404, detail="not a file")
    if archive_format(target.name) is None:
        raise HTTPException(status_code=415, detail="not a zip or tar archive")
    if target.stat().st_size > ARCHIVE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="archive too large to read")
    return target


def _clean(name: str) -> str:
    return name.replace("\\", "/").lstrip("/")


def list_archive(target: Path) -> dict:
    """Every entry of the archive, directories included, capped."""
    entries: dict[str, dict] = {}
    total = 0
    dropped = False

    def add(name: str, size: int, is_dir: bool) -> None:
        nonlocal total, dropped
        name = _clean(name).rstrip("/")
        if not name:
            return
        # Zip files often omit directory entries; make the parents exist
        # so the tree always has somewhere to hang a file.
        parts = name.split("/")
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            if parent not in entries:
                if len(entries) >= LIST_MAX_ENTRIES:
                    dropped = True
                    return
                entries[parent] = {"path": parent, "size": 0, "dir": True}
        if not is_dir:
            total += 1
        if name in entries:
            # A real directory entry after a file implied it: keep one row.
            return
        if len(entries) >= LIST_MAX_ENTRIES:
            dropped = True
            return
        entries[name] = {"path": name, "size": size, "dir": is_dir}

    try:
        if archive_format(target.name) == "zip":
            with zipfile.ZipFile(target) as archive:
                for info in archive.infolist():
                    add(info.filename, info.file_size, info.is_dir())
            fmt = "zip"
        else:
            with tarfile.open(target) as archive:
                for member in archive:
                    if member.isdir() or member.isfile():
                        add(member.name, member.size, member.isdir())
            fmt = "tar"
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError) as error:
        raise HTTPException(status_code=422, detail=f"cannot read archive: {error}")
    # ``total`` counts files, so a card can say "11 files" for the whole archive.
    return {"format": fmt, "entries": list(entries.values()), "total": total,
            "truncated": dropped}


def read_member(target: Path, member: str) -> bytes:
    """One member's bytes; 404 when absent, 413 when too large."""
    wanted = _clean(member)
    try:
        if archive_format(target.name) == "zip":
            with zipfile.ZipFile(target) as archive:
                info = next((item for item in archive.infolist()
                             if _clean(item.filename) == wanted and not item.is_dir()), None)
                if info is None:
                    raise HTTPException(status_code=404, detail="no such member")
                if info.file_size > MEMBER_MAX_BYTES:
                    raise HTTPException(status_code=413, detail="member too large to preview")
                with archive.open(info) as stream:
                    data = stream.read(MEMBER_MAX_BYTES + 1)
        else:
            with tarfile.open(target) as archive:
                found = next((item for item in archive
                              if item.isfile() and _clean(item.name) == wanted), None)
                if found is None:
                    raise HTTPException(status_code=404, detail="no such member")
                if found.size > MEMBER_MAX_BYTES:
                    raise HTTPException(status_code=413, detail="member too large to preview")
                stream = archive.extractfile(found)
                data = stream.read(MEMBER_MAX_BYTES + 1) if stream else b""
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError, RuntimeError) as error:
        # RuntimeError: an encrypted zip member without a password.
        raise HTTPException(status_code=422, detail=f"cannot read member: {error}")
    # A lying size header (zip bomb) is cut at the same limit.
    if len(data) > MEMBER_MAX_BYTES:
        raise HTTPException(status_code=413, detail="member too large to preview")
    return data


def register(app) -> None:
    import asyncio

    @app.get("/api/archive-list")
    async def archive_list(path: str, session_id: str = "", project_id: str = ""):
        target = _target(path, session_id, project_id)
        return JSONResponse(content=await asyncio.to_thread(list_archive, target))

    @app.get("/api/archive-member")
    async def archive_member(path: str, member: str, session_id: str = "", project_id: str = ""):
        target = _target(path, session_id, project_id)
        data = await asyncio.to_thread(read_member, target, member)
        headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
                   "Cache-Control": "no-store"}
        guessed = mimetypes.guess_type(os.path.basename(member))[0]
        if guessed and guessed.split("/")[0] in {"image", "video", "audio"}:
            return Response(data, media_type=guessed, headers=headers)
        if guessed == "application/pdf":
            headers.pop("Content-Security-Policy")
            return Response(data, media_type=guessed, headers=headers)
        return Response(data, media_type="application/octet-stream", headers=headers)
