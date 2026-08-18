from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any

import mcp.types as mcp_types

from openprogram.agent.types import AgentToolResult
from openprogram.providers.types import ImageContent, TextContent


_IMAGE_MEDIA_TYPE = re.compile(r"image/[!#$%&'*+\-.^_`|~0-9A-Za-z]+").fullmatch


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


def prompt_result(session_id: str, result: Any) -> AgentToolResult:
    """Project one validated dispatcher result onto the fixed MCP payload."""
    final_text = getattr(result, "final_text", None)
    assistant_msg_id = getattr(result, "assistant_msg_id", None)
    failed = getattr(result, "failed", None)
    if (
        type(session_id) is not str
        or not session_id
        or type(final_text) is not str
        or type(assistant_msg_id) is not str
        or not assistant_msg_id
        or type(failed) is not bool
    ):
        return json_result({"error": "prompt execution failed"}, is_error=True)
    return json_result(
        {
            "session_id": session_id,
            "text": final_text,
            "assistant_msg_id": assistant_msg_id,
            "failed": failed,
        }
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
                or _IMAGE_MEDIA_TYPE(block.mime_type) is None
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


__all__ = ["json_result", "prompt_result", "to_mcp_content"]
