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
_VERIFY_LIFT_FIELDS = (
    "expected_frame_id",
    "assertion",
    "value",
)
_OBSERVE_LIFT_FIELDS = ("url",)
_WEB_USE_CALL_KEYS = frozenset({
    "command",
    "backend",
    "page",
    "page_context_token",
    "web_session_id",
    "arguments",
    "runtime",
})
_ASSERTION_ENUM = (
    "text_contains",
    "text_not_contains",
    "url_contains",
    "title_contains",
    "element_present",
)
_ACT_ACTION_ENUM = (
    "screenshot",
    "navigate",
    "click",
    "type",
    "press",
    "scroll",
    "hover",
    "select",
)


def _url_property() -> dict[str, Any]:
    return {
        "type": "string",
        "description": (
            "http(s) URL. observe or act with this field opens a desktop "
            "web tab when no Page is available."
        ),
    }


def _expected_frame_id_property() -> dict[str, Any]:
    return {
        "type": "string",
        "description": (
            "Latest frame_id from observe. The runtime fills this when omitted."
        ),
    }


def _action_properties() -> dict[str, Any]:
    return {
        "action": {
            "type": "string",
            "enum": list(_ACT_ACTION_ENUM),
            "description": (
                "Required for act. Direct callers may pass it next to command; "
                "it is lifted into arguments."
            ),
        },
        "expected_frame_id": _expected_frame_id_property(),
        "ref": {"type": "string"},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "url": _url_property(),
        "text": {"type": "string"},
        "key": {"type": "string"},
        "value": {"type": "string"},
        "amount": {"type": "integer"},
        "assertion": {
            "type": "string",
            "enum": list(_ASSERTION_ENUM),
        },
    }


def _act_arguments_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": _action_properties(),
        "additionalProperties": False,
    }


def _verify_arguments_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["verify"],
                "description": "Use verify inside arguments.",
            },
            "expected_frame_id": _expected_frame_id_property(),
            "assertion": {
                "type": "string",
                "enum": list(_ASSERTION_ENUM),
            },
            "value": {"type": "string", "minLength": 1},
        },
        "required": ["assertion", "value"],
        "additionalProperties": False,
    }


def _observe_arguments_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "url": _url_property(),
            "detail": {
                "type": "string",
                "description": "Observation detail, for example interactive.",
            },
            "expected_frame_id": _expected_frame_id_property(),
        },
        "additionalProperties": False,
    }


def _arguments_schema() -> dict[str, Any]:
    return {
        "description": (
            "Command-specific arguments. act needs action; verify needs "
            "assertion and value; observe may include url and detail. "
            "Direct callers may still pass action, url, text, and ref next "
            "to command; they are lifted into arguments."
        ),
        "anyOf": [
            {"type": "null"},
            {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            _observe_arguments_schema(),
            _act_arguments_schema(),
            _verify_arguments_schema(),
        ],
    }


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _lift_fields_for(command: Any, action: Any) -> tuple[str, ...]:
    if command == "act":
        return _ACTION_FIELD_NAMES
    if command == "verify":
        if action == "verify":
            return ("action", *_VERIFY_LIFT_FIELDS)
        return _VERIFY_LIFT_FIELDS
    if command == "observe":
        return _OBSERVE_LIFT_FIELDS
    return ()


def normalize_web_use_arguments(args: Mapping[str, Any] | None) -> dict[str, Any]:
    """Lift command-applicable top-level fields into ``arguments``.

    Models that ignore ``allOf``/``if-then`` put ``action`` next to ``command``.
    Strict providers require every nested property and encode omission as
    JSON null; those nulls are dropped so canonical validation still sees
    omission. Numeric ``0`` stays. Already-nested non-null extras stay so
    command schemas can still reject them.
    """
    out = dict(args or {})
    had_nested = isinstance(out.get("arguments"), dict)
    nested = dict(out["arguments"]) if had_nested else {}
    action = out.get("action")
    if _blank(action):
        action = nested.get("action")
    for key in _lift_fields_for(out.get("command"), action):
        top = out.get(key)
        inner = nested.get(key)
        if not _blank(top) and key not in nested:
            nested[key] = top
        elif _blank(top) and not _blank(inner):
            out[key] = inner
    nested = {key: value for key, value in nested.items() if value is not None}
    if nested:
        out["arguments"] = nested
    elif had_nested:
        out["arguments"] = {}
    normalized = {}
    for key, value in out.items():
        if key not in _WEB_USE_CALL_KEYS:
            continue
        if value is None and key != "arguments":
            continue
        normalized[key] = value
    return normalized


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
            "arguments": _arguments_schema(),
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
                        "arguments": _act_arguments_schema(),
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
                        "arguments": _verify_arguments_schema(),
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
