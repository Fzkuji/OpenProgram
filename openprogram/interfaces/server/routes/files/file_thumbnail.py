"""``GET /api/file-thumbnail?path=...`` — an Office file's first page as an image.

A reply's file card shows what a file holds before anyone opens it. A
browser can draw images, PDFs, pages and text itself, but not a Word,
PowerPoint or Excel file: the in-app ONLYOFFICE editor is an optional
730 MB install and far too heavy to boot for every card. The worker
draws the picture instead, in this order:

1. macOS Quick Look (``qlmanage -t``), the system's own renderer: the
   first slide or page, fonts and all, in a fraction of a second.
2. The picture the authoring app saved inside the file
   (``docProps/thumbnail.*`` in Office Open XML, ``Thumbnails/thumbnail.png``
   in OpenDocument). PowerPoint and Word save one; files a script made
   usually have none.

Neither gives a picture → 404, and the card shows only the file's name.
Same root check as ``/api/file-raw``. Pictures are kept under the state
cache, keyed by the file's path, size and modification time, so a card
drawn again costs one ``stat``.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import Response

from openprogram.host._platform.filesystem import replace_file
from openprogram.state import attachments as _attach

_log = logging.getLogger(__name__)

#: Files the route draws: the formats the App's Office editor opens.
OFFICE_SUFFIXES = {".docx", ".pptx", ".xlsx", ".odt", ".odp", ".ods", ".doc", ".ppt", ".xls"}

#: Width of the drawn picture: sharp on a 440 px card at 2x.
_WIDTH = 1280
#: A renderer that takes longer than this is given up on.
_TIMEOUT_S = 20
#: Embedded pictures larger than this are not trusted as thumbnails.
_EMBEDDED_MAX_BYTES = 8 * 1024 * 1024

#: At most two renders at once: a transcript scrolled past many decks
#: must not start a Quick Look process per card.
_renders = asyncio.Semaphore(2)


def register(app) -> None:
    @app.get("/api/file-thumbnail")
    async def file_thumbnail(path: str, session_id: str = ""):
        target = _attach.resolve_session_attachment(
            path, session_id or None,
            _attach.readable_roots(session_id or None),
            allow_missing=True,
        )
        if target is None:
            raise HTTPException(status_code=403, detail="path not allowed")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not a file")
        if target.suffix.lower() not in OFFICE_SUFFIXES:
            raise HTTPException(status_code=415, detail="not an Office file")
        async with _renders:
            picture = await asyncio.to_thread(thumbnail, target)
        if picture is None:
            raise HTTPException(status_code=404, detail="no preview")
        data, media_type = picture
        return Response(data, media_type=media_type, headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "no-store",
        })


def thumbnail(target: Path) -> tuple[bytes, str] | None:
    """The first page of ``target`` as (bytes, media type), drawn once
    per version of the file and then read from the cache."""
    try:
        stat = target.stat()
    except OSError:
        return None
    key = hashlib.sha256(f"{target}\0{stat.st_size}\0{stat.st_mtime_ns}".encode()).hexdigest()
    folder = _cache_dir()
    for suffix, media_type in ((".png", "image/png"), (".jpeg", "image/jpeg")):
        cached = folder / f"{key}{suffix}"
        if cached.is_file():
            try:
                return cached.read_bytes(), media_type
            except OSError:
                break
    picture = _quick_look(target) or _embedded(target)
    if picture is None:
        return None
    data, media_type = picture
    try:
        folder.mkdir(parents=True, exist_ok=True)
        cached = folder / f"{key}{'.png' if media_type == 'image/png' else '.jpeg'}"
        partial = cached.with_suffix(cached.suffix + ".part")
        partial.write_bytes(data)
        replace_file(partial, cached)
    except OSError:
        _log.debug("thumbnail cache write failed for %s", target, exc_info=True)
    return picture


def _cache_dir() -> Path:
    from openprogram.state.paths import get_state_dir
    return Path(get_state_dir()) / "cache" / "file-thumbnails"


def _quick_look(target: Path) -> tuple[bytes, str] | None:
    """macOS Quick Look's picture of the file's first page."""
    if sys.platform != "darwin":
        return None
    qlmanage = shutil.which("qlmanage") or "/usr/bin/qlmanage"
    if not Path(qlmanage).is_file():
        return None
    with tempfile.TemporaryDirectory(prefix="openprogram-thumb-") as out:
        try:
            subprocess.run(
                [qlmanage, "-t", "-s", str(_WIDTH), "-o", out, str(target)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=_TIMEOUT_S, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            _log.debug("Quick Look failed for %s", target, exc_info=True)
            return None
        drawn = next(Path(out).glob("*.png"), None)
        if drawn is None:
            return None
        try:
            data = drawn.read_bytes()
        except OSError:
            return None
    return (data, "image/png") if data.startswith(b"\x89PNG") else None


def _embedded(target: Path) -> tuple[bytes, str] | None:
    """The picture the authoring app saved inside an Office Open XML or
    OpenDocument file, when it is a PNG or JPEG."""
    try:
        with zipfile.ZipFile(target) as archive:
            names = {info.filename.lower(): info for info in archive.infolist()}
            for name in ("docprops/thumbnail.png", "docprops/thumbnail.jpeg",
                         "docprops/thumbnail.jpg", "thumbnails/thumbnail.png"):
                info = names.get(name)
                if info is None or info.file_size > _EMBEDDED_MAX_BYTES:
                    continue
                data = archive.read(info)
                if data.startswith(b"\x89PNG"):
                    return data, "image/png"
                if data.startswith(b"\xff\xd8"):
                    return data, "image/jpeg"
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
        return None
    return None
