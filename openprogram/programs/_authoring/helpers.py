"""Small helpers shared by tool `execute` implementations.

Argument coercion — models call us with JSON where booleans sometimes
arrive as the string `"true"`, numbers as the string `"42"`, and snake
vs camel case disagrees between providers. Catch those at the edges so
individual tools don't each have to do it.

Availability gating — `is_available(tool)` checks a tool's `check_fn`
(if present) and the presence of all env vars in `requires_env`. Used
by the provider-picker in web_search/image_generate to decide which
backends to expose, and by `available_tools()` to list what's ready.
"""

from __future__ import annotations

import os
from typing import Any, Mapping


_TRUE_STRS = {"true", "1", "yes", "on", "y", "t"}
_FALSE_STRS = {"false", "0", "no", "off", "n", "f", ""}


def _norm(key: str) -> str:
    """Canonicalise a param name for cross-provider matching.

    OpenAI sends snake_case, some other APIs send camelCase, and ad-hoc
    tool wrappers sometimes arrive with Title Case. We normalise all
    three to the same lowercase underscore-less form so callers can
    list aliases without worrying about which form the model used.
    """
    return "".join(ch for ch in key.lower() if ch != "_")


def read_string_param(args: Mapping[str, Any], *names: str, default: str | None = None) -> str | None:
    """Look up a param by several name aliases (case-insensitive)."""
    if not args:
        return default
    normalised = {_norm(k): v for k, v in args.items()}
    for n in names:
        v = normalised.get(_norm(n))
        if v is not None:
            return str(v)
    return default


def read_bool_param(args: Mapping[str, Any], *names: str, default: bool = False) -> bool:
    """Parse a bool param, tolerating string inputs like `"true"`."""
    if not args:
        return default
    normalised = {_norm(k): v for k, v in args.items()}
    for n in names:
        if _norm(n) in normalised:
            v = normalised[_norm(n)]
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            s = str(v).strip().lower()
            if s in _TRUE_STRS:
                return True
            if s in _FALSE_STRS:
                return False
            return default
    return default


def read_int_param(args: Mapping[str, Any], *names: str, default: int | None = None) -> int | None:
    if not args:
        return default
    normalised = {_norm(k): v for k, v in args.items()}
    for n in names:
        if _norm(n) in normalised:
            v = normalised[_norm(n)]
            if isinstance(v, bool):
                return int(v)
            if isinstance(v, (int, float)):
                return int(v)
            try:
                return int(str(v).strip())
            except (TypeError, ValueError):
                return default
    return default


def has_env(names: list[str] | tuple[str, ...]) -> bool:
    """True iff every env var in `names` is set to a non-empty value."""
    return all(bool(os.environ.get(n)) for n in names)


def is_available(tool: dict) -> bool:
    """Return True if a legacy ``{spec, execute, check_fn?, requires_env?}``
    dict tool passes its gating.

    Order: `check_fn()` first (if provided) — a tool can veto itself for
    any reason. Then `requires_env` — every listed env var must be set.
    Tools without either key are always available.

    For ``@function``-decorated tools the equivalent helper is
    :func:`is_available_agent_tool`.
    """
    check_fn = tool.get("check_fn")
    if check_fn is not None:
        try:
            if not check_fn():
                return False
        except Exception:
            return False
    required = tool.get("requires_env") or []
    if required and not has_env(required):
        return False
    return True


def is_available_agent_tool(agent_tool: Any) -> bool:
    """Return True if the ``@function``-decorated AgentTool passes gating.

    Reads sidecar attributes the decorator sets:

      - ``_check_fn``: no-arg callable (process-level veto).
      - ``_requires_env``: env-var names that must all be set.
      - ``_can_use``: no-arg callable (session-level / role veto).

    Order matches :func:`is_available` for legacy dicts (check_fn,
    then requires_env), with ``can_use`` last because it's the most
    expensive gate (a real Slack/Telegram role check might hit the
    network in a future plugin).

    A tool with none of these attributes is always available.
    """
    check_fn = getattr(agent_tool, "_check_fn", None)
    if check_fn is not None:
        try:
            if not check_fn():
                return False
        except Exception:
            return False
    requires_env = getattr(agent_tool, "_requires_env", ())
    if requires_env and not has_env(requires_env):
        return False
    can_use = getattr(agent_tool, "_can_use", None)
    if can_use is not None:
        try:
            if not can_use():
                return False
        except Exception:
            return False
    return True


__all__ = [
    "read_string_param",
    "read_bool_param",
    "read_int_param",
    "has_env",
    "is_available",
    "is_available_agent_tool",
]
