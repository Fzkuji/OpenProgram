"""Shared public schema for OpenProgram Web Use tools."""

from __future__ import annotations


SUPPORTED_WEB_USE_BACKENDS = (
    "playwright_mcp",
    "chrome_devtools_mcp",
    "open_claude_chrome",
)


def web_use_parameters() -> dict:
    """Return a fresh command-conditioned Web Use JSON Schema."""
    backend_values = ["", *SUPPORTED_WEB_USE_BACKENDS]
    return {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": ["list_pages", "observe", "act", "verify", "close"],
                "description": (
                    "Call list_pages first, then observe, act, verify, or close"
                ),
            },
            "backend": {
                "type": "string",
                "enum": backend_values,
                "description": (
                    "Backend selected when observe creates a session. Omit or "
                    "use an empty string for the default backend."
                ),
            },
            "page": {
                "type": "string",
                "maxLength": 512,
                "description": "A Page alias from the current turn; never a URL",
            },
            "page_context_token": {"type": "string", "maxLength": 128},
            "web_session_id": {"type": "string", "maxLength": 128},
            "arguments": {"type": "object", "additionalProperties": True},
        },
        "required": ["command"],
        "allOf": [
            {
                "if": {
                    "properties": {"command": {"const": "act"}},
                    "required": ["command"],
                },
                "then": {
                    "required": ["web_session_id", "arguments"],
                    "properties": {
                        "web_session_id": {"type": "string", "minLength": 1},
                        "arguments": {
                            "type": "object",
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "enum": [
                                        "screenshot", "navigate", "click", "type",
                                        "press", "scroll", "hover", "select",
                                    ],
                                },
                                "expected_frame_id": {
                                    "type": "string",
                                    "minLength": 1,
                                    "description": (
                                        "Latest frame_id returned by observe"
                                    ),
                                },
                                "ref": {"type": "string"},
                                "x": {"type": "number"},
                                "y": {"type": "number"},
                                "url": {"type": "string"},
                                "text": {"type": "string"},
                                "key": {"type": "string"},
                                "value": {"type": "string"},
                                "amount": {"type": "integer"},
                            },
                            "required": ["action", "expected_frame_id"],
                            "additionalProperties": False,
                        },
                    },
                },
            },
            {
                "if": {
                    "properties": {"command": {"const": "verify"}},
                    "required": ["command"],
                },
                "then": {
                    "required": ["web_session_id", "arguments"],
                    "properties": {
                        "web_session_id": {"type": "string", "minLength": 1},
                        "arguments": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "const": "verify"},
                                "expected_frame_id": {
                                    "type": "string",
                                    "minLength": 1,
                                },
                                "assertion": {
                                    "type": "string",
                                    "enum": [
                                        "text_contains", "text_not_contains",
                                        "url_contains", "title_contains",
                                        "element_present",
                                    ],
                                },
                                "value": {"type": "string", "minLength": 1},
                            },
                            "required": [
                                "expected_frame_id", "assertion", "value",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
            },
            {
                "if": {
                    "properties": {"command": {"const": "close"}},
                    "required": ["command"],
                },
                "then": {
                    "required": ["web_session_id"],
                    "properties": {
                        "web_session_id": {"type": "string", "minLength": 1},
                    },
                },
            },
        ],
        "additionalProperties": False,
    }


__all__ = ["SUPPORTED_WEB_USE_BACKENDS", "web_use_parameters"]
