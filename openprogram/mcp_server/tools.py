from __future__ import annotations

import base64
import binascii
import json
from typing import Any

import mcp.types as mcp_types

from openprogram.agent.types import AgentToolResult
from openprogram.providers.types import ImageContent, TextContent


def json_result(payload: Any, *, is_error: bool = False) -> AgentToolResult:
    """Return one deterministic JSON text block with a first-class error bit."""
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        text.encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        text = '{"error":"result serialization failed"}'
        is_error = True
    return AgentToolResult(
        content=[TextContent(type="text", text=text)],
        is_error=is_error,
    )


def to_mcp_content(result: AgentToolResult) -> list[mcp_types.ContentBlock]:
    """Convert supported Runtime blocks without inventing a fallback shape."""
    converted: list[mcp_types.ContentBlock] = []
    for block in result.content:
        if isinstance(block, TextContent):
            converted.append(mcp_types.TextContent(type="text", text=block.text))
        elif isinstance(block, ImageContent):
            if (
                type(block.data) is not str
                or not block.data
                or type(block.mime_type) is not str
                or not block.mime_type.startswith("image/")
                or not block.mime_type.removeprefix("image/")
                or any(char.isspace() for char in block.mime_type)
            ):
                raise ValueError("unsupported Runtime tool content")
            try:
                base64.b64decode(block.data, validate=True)
            except (ValueError, binascii.Error):
                raise ValueError("unsupported Runtime tool content") from None
            converted.append(
                mcp_types.ImageContent(
                    type="image",
                    data=block.data,
                    mimeType=block.mime_type,
                )
            )
        else:
            raise ValueError("unsupported Runtime tool content")
    return converted


__all__ = ["json_result", "to_mcp_content"]
