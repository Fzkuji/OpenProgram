"""Input modality validation — raises before any HTTP call is made."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.models.providers.types import Context, Model

_MODALITY_TYPES = {"image", "video", "audio"}


def validate_input_modalities(model: "Model", context: "Context") -> None:
    """Raise ValueError if the newest message a person sent contains a
    modality not in model.input.

    Only that message is checked: it is what the person is sending now.
    An image earlier in history (kept by ``store.session.media``) reaches a
    model that does not take images as a note instead
    (``media_budget.without_unsupported_images``), and assistant/toolResult
    content is always safe to replay, since it was produced by a prior call.
    """
    from .tool_attachments import AttachmentMessage

    supported = set(model.input or ["text"])
    unsupported_found: set[str] = set()

    newest = [msg for msg in context.messages
              if getattr(msg, "role", None) == "user" and not isinstance(msg, AttachmentMessage)][-1:]
    for msg in newest:
        content = getattr(msg, "content", None)
        if not content or isinstance(content, str):
            continue
        for item in content:
            t = getattr(item, "type", None)
            if t in _MODALITY_TYPES and t not in supported:
                unsupported_found.add(t)

    if unsupported_found:
        missing = ", ".join(sorted(unsupported_found))
        raise ValueError(
            f"Model '{model.id}' does not support input modality: {missing}. "
            f"Supported: {sorted(supported)}"
        )
