from __future__ import annotations

import json
from typing import Any

from openprogram.agent.types import AgentToolResult
from openprogram.providers.types import TextContent


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


__all__ = ["json_result"]
