"""Input modality validation — raises before any HTTP call is made."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.providers.types import Context, Model

_MODALITY_TYPES = {"image", "video", "audio"}


def validate_input_modalities(model: "Model", context: "Context") -> None:
    """Raise ValueError if any user message contains a modality not in model.input.

    Only checks user messages — assistant/toolResult content is always safe to
    replay regardless of modality, since it was produced by a prior call.
    """
    supported = set(model.input or ["text"])
    unsupported_found: set[str] = set()

    for msg in context.messages:
        if getattr(msg, "role", None) != "user":
            continue
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
