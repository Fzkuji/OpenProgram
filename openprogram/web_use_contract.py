"""Shared public schema for OpenProgram Web Use tools."""

from __future__ import annotations

from typing import Any, Mapping


SUPPORTED_WEB_USE_BACKENDS = (
    "playwright_mcp",
    "chrome_devtools_mcp",
    "open_claude_chrome",
)

_ACTION_FIELD_NAMES = (
    "action",
    "expected_frame_id",
    "ref",
    "x",
    "y",
    "url",
    "text",
    "key",
    "value",
    "amount",
    "assertion",
)
_WEB_USE_CALL_KEYS = frozenset({
    "command",
    "backend",
    "page",
    "page_context_token",
    "web_session_id",
    "arguments",
    "runtime",
})


def _action_properties() -> dict[str, Any]:
    return {
        "action": {
            "type": "string",
            "enum": [
                "screenshot", "navigate", "click", "type",
                "press", "scroll", "hover", "select",
            ],
            "description": (
                "Required for act. Accepted at the top level or inside arguments."
            ),
        },
        "expected_frame_id": {
            "type": "string",
            "description": (
                "Latest frame_id from observe. The runtime fills this when omitted."
            ),
        },
        "ref": {"type": "string"},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "url": {
            "type": "string",
            "description": (
                "http(s) URL. observe or act with this field opens a desktop "
                "web tab when no Page is available."
            ),
        },
        "text": {"type": "string"},
        "key": {"type": "string"},
        "value": {"type": "string"},
        "amount": {"type": "integer"},
        "assertion": {
            "type": "string",
            "enum": [
                "text_contains", "text_not_contains",
                "url_contains", "title_contains",
                "element_present",
            ],
        },
    }


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def normalize_web_use_arguments(args: Mapping[str, Any] | None) -> dict[str, Any]:
    """Lift top-level act fields into ``arguments`` so callers match the schema.

    Models that ignore ``allOf``/``if-then`` put ``action`` next to ``command``.
    ``expected_frame_id`` stays optional here; the session runtime fills it.
    """
    out = dict(args or {})
    nested = dict(out["arguments"]) if isinstance(out.get("arguments"), dict) else {}
    for key in _ACTION_FIELD_NAMES:
        top = out.get(key)
        inner = nested.get(key)
        if not _blank(top) and key not in nested:
            nested[key] = top
        elif _blank(top) and not _blank(inner):
            out[key] = inner
    if nested:
        out["arguments"] = nested
    return {key: value for key, value in out.items() if key in _WEB_USE_CALL_KEYS}


def web_use_parameters() -> dict:
    """Return a fresh command-conditioned Web Use JSON Schema."""
    backend_values = ["", *SUPPORTED_WEB_USE_BACKENDS]
    action_properties = _action_properties()
    return {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": ["list_pages", "observe", "act", "verify", "close"],
                "description": (
                    "Call list_pages first, then observe, act, verify, or close. "
                    "observe or act with url opens a desktop web tab when no "
                    "Page exists."
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
            "web_session_id": {
                "type": "string",
                "maxLength": 128,
                "description": (
                    "Session id returned by observe. Do not invent placeholders "
                    "such as pending; omit it and the runtime reuses the latest "
                    "session for this turn."
                ),
            },
            "arguments": {
                "type": "object",
                "additionalProperties": True,
                "description": (
                    "Command-specific arguments. act needs action; "
                    "expected_frame_id is filled from the last observe."
                ),
            },
            **action_properties,
        },
        "required": ["command"],
        "allOf": [
            {
                "if": {
                    "properties": {"command": {"const": "act"}},
                    "required": ["command"],
                },
                "then": {
                    "properties": {
                        "web_session_id": {"type": "string"},
                        "arguments": {
                            "type": "object",
                            "properties": action_properties,
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
                    "required": ["web_session_id"],
                    "properties": {
                        "web_session_id": {"type": "string", "minLength": 1},
                        "arguments": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "const": "verify"},
                                "expected_frame_id": {"type": "string"},
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
                            "required": ["assertion", "value"],
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


__all__ = [
    "SUPPORTED_WEB_USE_BACKENDS",
    "normalize_web_use_arguments",
    "web_use_parameters",
]
