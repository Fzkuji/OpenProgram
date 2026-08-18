"""The schema dialects — pure ``schema -> schema`` transforms, one per
wire format an API accepts.

Kept in one file on purpose: the single most important fact about this
layer is that the dialects *disagree* — OpenAI strict **adds**
``additionalProperties: false`` to every object, Gemini's OpenAPI mode
**strips** it. Seeing the two side by side is the documentation.

  ┌─────────────────┬──────────────────────────────────────────────┐
  │ openai_strict   │ ADD additionalProperties:false, promote all   │
  │                 │ props to required (optional → nullable),       │
  │                 │ strip facet keywords. (in strict.py)           │
  ├─────────────────┼──────────────────────────────────────────────┤
  │ gemini_openapi  │ STRIP additionalProperties + the combinator / │
  │                 │ meta keywords Gemini's OpenAPI-3.0 subset      │
  │                 │ rejects (anyOf, oneOf, $schema, const, …).     │
  ├─────────────────┼──────────────────────────────────────────────┤
  │ passthrough     │ identity (deep-copied). Anthropic / Bedrock /  │
  │                 │ Gemini parametersJsonSchema mode / unknown.    │
  └─────────────────┴──────────────────────────────────────────────┘

The big, stable ``openai_strict`` transform lives in its own
``strict.py`` (it's ~180 lines and well-tested); it's re-exported here
so the registry in ``__init__`` can treat all three uniformly.
"""

from __future__ import annotations

import copy
from typing import Any

# Re-export so the dispatcher registry can reference all dialects from
# one module. ``openai_strict`` is just ``fixup_for_strict`` under the
# dialect-name spelling.
from .strict import fixup_for_strict as openai_strict  # noqa: F401


# Keywords Gemini's ``parameters`` (OpenAPI 3.0 subset) mode rejects.
# Verified against Google's own google-genai SDK client-side validation
# (github.com/googleapis/python-genai issue #1815) + the Gemini function-
# calling docs. Sending any of these gets the whole request 400'd.
_GEMINI_UNSUPPORTED_KEYWORDS: frozenset[str] = frozenset({
    "additionalProperties",
    "$schema",
    "$id",
    "$ref",
    "$defs",
    "definitions",
    "patternProperties",
    "propertyNames",
    "const",
    "if",
    "then",
    "else",
    "not",
    "allOf",
    "anyOf",
    "oneOf",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "examples",
    "contentEncoding",
    "contentMediaType",
})


def passthrough(schema: dict[str, Any]) -> dict[str, Any]:
    """Identity transform — deep-copied so callers can't mutate the
    canonical tool schema. Used by APIs that accept standard JSON
    Schema as-is (Anthropic, Bedrock, Gemini's fuller
    ``parametersJsonSchema`` mode)."""
    return copy.deepcopy(schema) if isinstance(schema, dict) else schema


def gemini_openapi(schema: dict[str, Any]) -> dict[str, Any]:
    """Rewrite a schema for Gemini's ``parameters`` (OpenAPI 3.0 subset)
    mode by recursively removing the keywords that mode doesn't accept.

    The mirror image of ``openai_strict``: where strict *adds*
    ``additionalProperties: false``, this *removes* it (Gemini rejects
    it in OpenAPI mode), along with the combinator keywords (anyOf /
    oneOf / allOf / not) and JSON-Schema meta keywords ($schema, $ref,
    const, if/then/else, …). ``enum``, ``type``, ``properties``,
    ``items``, ``required``, ``description``, ``minimum``/``maximum``
    and ``format`` are kept — those are in the OpenAPI subset.
    """
    if not isinstance(schema, dict):
        return schema
    out = copy.deepcopy(schema)
    _strip_gemini_in_place(out)
    return out


def _strip_gemini_in_place(node: Any) -> None:
    if isinstance(node, dict):
        for kw in list(node.keys()):
            if kw in _GEMINI_UNSUPPORTED_KEYWORDS:
                node.pop(kw, None)
        # Recurse into the structural children that survive.
        props = node.get("properties")
        if isinstance(props, dict):
            for sub in props.values():
                _strip_gemini_in_place(sub)
        items = node.get("items")
        if isinstance(items, dict):
            _strip_gemini_in_place(items)
        elif isinstance(items, list):
            for it in items:
                _strip_gemini_in_place(it)
    elif isinstance(node, list):
        for item in node:
            _strip_gemini_in_place(item)


__all__ = ["openai_strict", "gemini_openapi", "passthrough"]
