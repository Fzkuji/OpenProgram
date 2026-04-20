"""
Partial JSON parsing for streaming tool arguments — mirrors packages/ai/src/utils/json-parse.ts
"""
from __future__ import annotations

import json
import re
from typing import Any


def parse_partial_json(text: str) -> dict[str, Any] | None:
    # Alias for compatibility
    return _parse_partial_json_impl(text)


def parse_streaming_json(text: str) -> dict[str, Any]:
    """
    Parse potentially incomplete JSON from a streaming response.
    Returns empty dict {} on failure (mirrors TS behavior).
    """
    result = _parse_partial_json_impl(text)
    return result if result is not None else {}  # Return {} instead of None


def _parse_partial_json_impl(text: str) -> dict[str, Any] | None:
    """
    Parse potentially incomplete JSON from a streaming response.

    Tries exact parse first, then attempts to fix common truncation issues.
    Returns None if the text cannot be parsed even partially.
    """
    if not text or not text.strip():
        return None

    # Try exact parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to complete truncated JSON by closing open structures
    fixed = _try_fix_json(text)
    if fixed is not None:
        return fixed

    return None


def _try_fix_json(text: str) -> dict[str, Any] | None:
    """Attempt to fix truncated JSON by closing unclosed delimiters."""
    stripped = text.strip()

    # Must start with {
    if not stripped.startswith("{"):
        return None

    # Count open/close braces and brackets, handle strings
    stack: list[str] = []
    in_string = False
    escape_next = False

    for char in stripped:
        if escape_next:
            escape_next = False
            continue
        if char == "\\" and in_string:
            escape_next = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in ("}",  "]"):
            if stack and stack[-1] == char:
                stack.pop()

    # Close unclosed structures
    closing = "".join(reversed(stack))
    candidate = stripped + closing

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None
