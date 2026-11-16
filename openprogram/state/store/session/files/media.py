"""Images and other media of a message, kept so later requests send them again.

A turn sends a tool result's screenshot, or a person's pasted picture, as
an image block. History keeps the node's text, and the media here: each
block's bytes once, named by their content, in the conversation's
``media/<sha256>.<ext>`` (written to a temporary file, then put in place,
as ``tool_results/`` is), and the node records where each one sat in the
message (``metadata.media``: ``[{"sha", "mime", "index", "path"}]``).
Rendering puts the blocks back where they were, so the next turn's request
starts with the bytes the turn sent (Codex and Claude Code keep images in
history the same way; compaction leaves them out of what it summarizes).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

DIRNAME = "media"

_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
               "image/webp": ".webp", "audio/wav": ".wav", "audio/mpeg": ".mp3",
               "video/mp4": ".mp4"}


def _extension(mime: str) -> str:
    return _EXTENSIONS.get(mime) or mimetypes.guess_extension(mime or "") or ".bin"


def save(session_dir: str | os.PathLike | None, data_b64: str, mime: str) -> dict | None:
    """Keep one block's bytes in ``session_dir``/media and return its record
    ``{"sha", "mime", "path"}``, or None when they cannot be kept."""
    if not session_dir or not isinstance(data_b64, str) or not data_b64:
        return None
    try:
        raw = base64.b64decode(data_b64, validate=False)
    except (binascii.Error, ValueError):
        return None
    sha = hashlib.sha256(raw).hexdigest()
    directory = Path(session_dir) / DIRNAME
    path = directory / f"{sha}{_extension(mime)}"
    if not path.is_file():
        try:
            directory.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=f".{sha[:16]}-", suffix=".tmp", dir=directory)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                from openprogram.host._platform.filesystem import replace_file
                # Same name, same bytes: a writer that got here first left
                # this file, so replacing it changes nothing.
                replace_file(temporary, path)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        except OSError:
            return None
    return {"sha": sha, "mime": mime, "path": str(path)}


def records_for_blocks(session_dir: Any, blocks: Iterable[Any]) -> list[dict]:
    """``metadata.media`` for a message's content blocks: every block with
    bytes (an image, audio or video), at its index in ``blocks``."""
    records: list[dict] = []
    for index, block in enumerate(blocks or ()):
        data = getattr(block, "data", None)
        mime = getattr(block, "mime_type", None)
        if not isinstance(data, str) or not isinstance(mime, str):
            continue
        saved = save(session_dir, data, mime)
        if saved is not None:
            records.append({**saved, "index": index})
    return records


def records_for_attachments(session_dir: Any, attachments: Iterable[Any], first_index: int) -> list[dict]:
    """``metadata.media`` for a person's message: its image attachments, in
    the order the turn puts them after the text, from ``first_index``."""
    records: list[dict] = []
    index = first_index
    for attachment in attachments or ():
        if not isinstance(attachment, dict) or attachment.get("type") != "image":
            continue
        saved = save(session_dir, attachment.get("data") or "",
                     attachment.get("media_type") or "image/png")
        if saved is not None:
            records.append({**saved, "index": index})
        index += 1
    return records


def attachments_from_records(records: Any, session_dir: Any = None) -> list[dict]:
    """A kept message's images as the attachments a turn sends, in order:
    what a retry of that message sends again. A record whose file is gone
    is left out."""
    attachments: list[dict] = []
    for record in sorted((r for r in records or () if isinstance(r, dict)),
                         key=lambda r: r.get("index") if isinstance(r.get("index"), int) else 0):
        mime = str(record.get("mime") or "")
        if not mime.startswith("image/"):
            continue
        data = load(record, session_dir)
        if data is not None:
            attachments.append({"type": "image", "data": data, "media_type": mime})
    return attachments


def load(record: dict, session_dir: Any = None) -> str | None:
    """The base64 data of a kept block, or None when its file is gone."""
    if not isinstance(record, dict) or not isinstance(record.get("sha"), str):
        return None
    candidates = []
    if session_dir:
        candidates.append(Path(session_dir) / DIRNAME
                          / f"{record['sha']}{_extension(str(record.get('mime') or ''))}")
    if isinstance(record.get("path"), str):
        candidates.append(Path(record["path"]))
    for path in candidates:
        try:
            return base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            continue
    return None


def block(record: dict, data: str) -> Any:
    """The content block a kept record renders as."""
    from openprogram.models.providers.types import AudioContent, ImageContent, VideoContent

    mime = str(record.get("mime") or "")
    if mime.startswith("audio/"):
        return AudioContent(data=data, mime_type=mime)
    if mime.startswith("video/"):
        return VideoContent(data=data, mime_type=mime)
    return ImageContent(data=data, mime_type=mime)


def with_media(text_blocks: list[Any], records: Any, session_dir: Any = None) -> list[Any]:
    """A message's content: ``text_blocks`` with the kept media put back at
    the indexes they had. A block whose file is gone is left out."""
    if not isinstance(records, list) or not records:
        return text_blocks
    placed: dict[int, Any] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("index"), int):
            continue
        data = load(record, session_dir)
        if data is not None:
            placed[record["index"]] = block(record, data)
    if not placed:
        return text_blocks
    content = list(text_blocks)
    for index in sorted(placed):
        content.insert(min(index, len(content)), placed[index])
    return content

__all__ = ["DIRNAME", "attachments_from_records", "block", "load", "records_for_attachments",
           "records_for_blocks",
           "save", "with_media"]
