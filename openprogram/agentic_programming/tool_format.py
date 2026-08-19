"""Convert an ``@agentic_function`` spec into other frameworks' tool formats.

``fn.spec`` is flat — ``{"name", "description", "parameters"}`` — which is what
the Responses API and this repo's own dispatcher consume. Hosts driving their
own loop through the Chat Completions ``tools=[...]`` array need the same
schema nested under a ``"function"`` key. That reshaping is the whole job here.

Embedding usage: build the tool list, hand it to your client, then dispatch a
returned tool call straight back through ``fn.execute(**arguments)``.
"""

from __future__ import annotations

from typing import Any

_EMPTY_PARAMETERS = {"type": "object", "properties": {}}


def to_openai_tool(function: Any) -> dict:
    """Return one OpenAI Chat Completions ``tools`` entry for ``function``.

    Accepts an ``@agentic_function`` (anything exposing ``.spec``) or an
    already-built flat spec dict.
    """
    spec = getattr(function, "spec", function)
    if not isinstance(spec, dict):
        raise TypeError(
            f"expected an @agentic_function or a spec dict, got {type(function).__name__}"
        )
    name = spec.get("name")
    if not name:
        raise ValueError("tool spec has no 'name'")
    return {
        "type": "function",
        "function": {
            "name": name,
            # Chat Completions rejects a null description; an unwritten
            # docstring falls back to the name rather than omitting the key.
            "description": spec.get("description") or name,
            "parameters": spec.get("parameters") or dict(_EMPTY_PARAMETERS),
        },
    }


def to_openai_tools(functions) -> list[dict]:
    """Map ``to_openai_tool`` over an iterable of @agentic_functions."""
    return [to_openai_tool(f) for f in functions]


__all__ = ["to_openai_tool", "to_openai_tools"]
