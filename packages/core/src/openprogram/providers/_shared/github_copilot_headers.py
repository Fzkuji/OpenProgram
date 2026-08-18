"""
GitHub Copilot header utilities.

Builds dynamic request headers for Copilot API requests, including
vision detection and initiator inference.

Mirrors github-copilot-headers.ts
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.providers.types import Message


def infer_copilot_initiator(messages: "list[Message]") -> str:
    """Return 'agent' if last message is not from user, else 'user'."""
    if not messages:
        return "user"
    last = messages[-1]
    return "agent" if last.role != "user" else "user"


def has_copilot_vision_input(messages: "list[Message]") -> bool:
    """Return True if any message contains an image content block."""
    for msg in messages:
        if msg.role in ("user", "toolResult") and isinstance(msg.content, list):
            if any(getattr(c, "type", None) == "image" for c in msg.content):
                return True
    return False


def build_copilot_dynamic_headers(messages: "list[Message]", has_images: bool) -> dict[str, str]:
    """Build Copilot-specific request headers."""
    headers: dict[str, str] = {
        "X-Initiator": infer_copilot_initiator(messages),
        "Openai-Intent": "conversation-edits",
    }
    if has_images:
        headers["Copilot-Vision-Request"] = "true"
    return headers
