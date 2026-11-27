"""A message's kept media as model content blocks.

``state.store.session.files.media`` keeps each block's bytes in the
conversation's folder and records where it sat in the message; rendering
history (``session_dag.render``) puts the blocks back here, or names the
files of an earlier message instead of attaching them again.
"""
from __future__ import annotations

from typing import Any

from openprogram.models.providers.types import AudioContent, ImageContent, TextContent, VideoContent
from openprogram.state.store.session.files.media import kept_path, load


def block(record: dict, data: str) -> Any:
    """The content block a kept record renders as."""
    mime = str(record.get("mime") or "")
    if mime.startswith("audio/"):
        return AudioContent(data=data, mime_type=mime)
    if mime.startswith("video/"):
        return VideoContent(data=data, mime_type=mime)
    return ImageContent(data=data, mime_type=mime)


def as_references(text_blocks: list[Any], records: Any, session_dir: Any = None) -> list[Any]:
    """A message's content with its media named, not attached: an earlier
    message's pictures, once a later message carries its own (Hermes sends
    only the latest; each picture otherwise rides on every request). The note
    gives each file's path so the model can look again."""
    if not isinstance(records, list) or not records:
        return text_blocks
    names = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("sha"), str):
            continue
        mime = str(record.get("mime") or "")
        path = kept_path(record, session_dir) if session_dir else record.get("path")
        names.append(f"{mime or 'file'} {path}")
    if not names:
        return text_blocks
    note = ("[Attachments of this earlier message, no longer attached to save context; "
            "open them with image_analyze if they are needed again: " + "; ".join(names) + "]")
    return [*text_blocks, TextContent(type="text", text=note)]


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


__all__ = ["as_references", "block", "with_media"]
