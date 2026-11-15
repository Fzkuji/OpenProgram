"""The last step of building a request: media the request cannot carry.

History keeps every image a turn sent (``store.session.media``), so a long
session of screenshots can grow past what a provider takes in one request.
Two rules, both pure functions of the messages, so a later request sends
what an earlier one did until the rule itself moves:

* **Size.** Inline images are replayed oldest first; whenever the kept
  bytes pass ``IMAGE_BYTES_TRIGGER`` (25 MiB) the oldest are replaced by a
  short note until they are under ``IMAGE_BYTES_TARGET`` (15 MiB). A new
  image moves the cut only once per 10 MiB, not on every request, so the
  prefix before the cut, and the provider's cache of it, stays put between
  cuts (OpenCode's ``boundImages``).
* **Modality.** A person's image sent to a model that does not take images
  is replaced by a note (Codex and OpenCode do the same). The newest
  message is checked before this and refused
  (``validate_modalities``); this covers the history of a conversation
  that moved to such a model.
"""
from __future__ import annotations

from typing import Any

from ..types import ImageContent, TextContent

IMAGE_BYTES_TRIGGER = 25 * 1024 * 1024
IMAGE_BYTES_TARGET = 15 * 1024 * 1024

IMAGE_REMOVED = ("[An image was here; it was removed to keep the request within its size "
                 "limit. Do not describe it from memory: read the file again or ask for it "
                 "if it is needed.]")
IMAGE_OMITTED = "[image omitted: model does not accept image input]"


def _image_blocks(message: Any) -> list[ImageContent]:
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, ImageContent)]


def _replace(message: Any, keep: Any) -> Any:
    """``message`` with each image ``keep(block)`` answers with a string
    replaced by that text."""
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return message
    changed = False
    out: list[Any] = []
    for block in content:
        note = keep(block) if isinstance(block, ImageContent) else None
        if note is None:
            out.append(block)
        else:
            out.append(TextContent(text=note))
            changed = True
    return message.model_copy(update={"content": out}) if changed else message


def bound_images(messages: list[Any]) -> list[Any]:
    """``messages`` with the oldest images replaced once the kept bytes pass
    the trigger, down to the target."""
    sizes = [len(block.data or "") for message in messages for block in _image_blocks(message)]
    cut = kept = 0
    for size in sizes:
        kept += size
        if kept <= IMAGE_BYTES_TRIGGER:
            continue
        while kept > IMAGE_BYTES_TARGET and cut < len(sizes):
            kept -= sizes[cut]
            cut += 1
    if cut == 0:
        return messages
    seen = 0

    def keep(_block: Any) -> str | None:
        nonlocal seen
        seen += 1
        return IMAGE_REMOVED if seen <= cut else None

    return [_replace(message, keep) for message in messages]


def without_unsupported_images(messages: list[Any], model: Any) -> list[Any]:
    """``messages`` with a person's images replaced by a note when ``model``
    does not take images."""
    if "image" in (getattr(model, "input", None) or ["text"]):
        return messages
    return [_replace(message, lambda _block: IMAGE_OMITTED)
            if getattr(message, "role", None) == "user" else message
            for message in messages]


__all__ = ["IMAGE_BYTES_TARGET", "IMAGE_BYTES_TRIGGER", "IMAGE_OMITTED", "IMAGE_REMOVED",
           "bound_images", "without_unsupported_images"]
