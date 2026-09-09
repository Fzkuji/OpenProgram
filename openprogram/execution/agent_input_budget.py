"""Shared size accounting for durable Agent turn inputs.

This module deliberately has no Agent runtime imports.  Both the production
admission boundary and the execution store validate the same representation:
image base64 is checked against image budgets, then omitted from the value used
for the ordinary text/metadata JSON budget.
"""

from __future__ import annotations

import base64
import binascii
import copy
from typing import Any, Mapping


AGENT_TURN_INPUT_MAX_BYTES = 256 * 1024
AGENT_IMAGE_BYTES_MAX = 5 * 1024 * 1024
AGENT_TURN_IMAGE_BYTES_MAX = 20 * 1024 * 1024
AGENT_IMAGE_COUNT_MAX = 20
_IMAGE_MEDIA_TYPES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
})


class AgentInputBudgetError(ValueError):
    """An image-specific input budget or encoding check failed."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def budget_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Copy *value* and blank validated image data for text-budget accounting.

    The returned copy is only for measuring the ordinary 256 KiB JSON input
    budget.  The caller keeps the original image data in the durable payload
    after this function validates its separate byte and count limits.
    """
    result = copy.deepcopy(dict(value))
    if result.get("kind") != "chat":
        return result
    request = result.get("request")
    if not isinstance(request, Mapping):
        return result
    attachments = request.get("attachments")
    if attachments is None:
        return result
    if not isinstance(attachments, list) or len(attachments) > AGENT_IMAGE_COUNT_MAX:
        raise AgentInputBudgetError("invalid_input", "Invalid image attachment list")

    total_image_bytes = 0
    for index, attachment in enumerate(attachments):
        if not isinstance(attachment, dict) or attachment.get("type") != "image":
            continue
        data = attachment.get("data")
        if (
            not isinstance(data, str)
            or not data
            or len(data) > 4 * ((AGENT_IMAGE_BYTES_MAX + 2) // 3)
        ):
            raise AgentInputBudgetError(
                "image_too_large", "Image is empty or exceeds the image size limit"
            )
        if attachment.get("media_type") not in _IMAGE_MEDIA_TYPES:
            raise AgentInputBudgetError("invalid_input", "Unsupported image format")
        try:
            size = len(base64.b64decode(data, validate=True))
        except (ValueError, binascii.Error) as exc:
            raise AgentInputBudgetError("invalid_input", "Invalid image encoding") from exc
        total_image_bytes += size
        if size > AGENT_IMAGE_BYTES_MAX or total_image_bytes > AGENT_TURN_IMAGE_BYTES_MAX:
            raise AgentInputBudgetError("image_too_large", "Images exceed the image size limit")
        attachments[index] = {**attachment, "data": ""}
    return result
