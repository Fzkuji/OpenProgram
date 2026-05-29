"""Rewrite arbitrary JSON Schema for OpenAI strict mode compatibility.

OpenAI's structured-outputs / strict-mode validation is a constrained
subset of JSON Schema:

  * Every ``object`` schema MUST set ``additionalProperties: false``.
  * Every property declared under an object MUST appear in the
    ``required`` list. Optional fields are expressed by making the
    type a union with ``"null"`` instead of omitting from ``required``.
  * A handful of JSON Schema keywords are not supported and silently
    rejected on the server: ``pattern``, ``format``, ``minLength``,
    ``maxLength``, ``minimum``, ``maximum``, ``multipleOf``, ``minItems``,
    ``maxItems``, ``uniqueItems``, ``default``, ``contentEncoding``,
    ``contentMediaType``, ``examples``, ``$ref`` (in the function-tool
    flavour). Leaving them in a tool definition gets the whole
    completion rejected with 400.

  * Optional ``enum`` works fine and is honoured by constrained decoding
    — the LLM literally cannot emit a value outside the enum.

  * Top-level schema MUST be an ``object``.

This module's ``fixup_for_strict()`` takes a copy of any schema and
applies the rewrites in-place on the copy so the original tool
definition (used by other providers + the docs / catalog) is untouched.

References:
  * https://platform.openai.com/docs/guides/structured-outputs
  * https://openai.com/index/introducing-structured-outputs-in-the-api/
"""

from __future__ import annotations

import copy
from typing import Any


# JSON Schema keywords OpenAI's strict mode doesn't accept. Some are
# numeric/string facet validators (pattern, minimum, …); some are
# documentation-only sugar (default, examples) we'd just lose.
# Stripping them is safe because the LLM constrained-decoding contract
# is "match the schema as declared" — anything we'd express via these
# keywords gets enforced via type / enum / properties instead.
_UNSUPPORTED_KEYWORDS: frozenset[str] = frozenset({
    "pattern",
    "format",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minItems",
    "maxItems",
    "uniqueItems",
    "default",
    "examples",
    "contentEncoding",
    "contentMediaType",
    "$ref",
    "$defs",
    "definitions",
    # ``anyOf`` / ``oneOf`` / ``allOf`` ARE supported but only when
    # they're at the property level and resolve to disjoint sets — we
    # leave them in place and let the API reject if mis-formed.
})


def fixup_for_strict(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``schema`` rewritten for OpenAI strict mode.

    Three rewrites applied recursively to every nested ``object``:

      1. Force ``additionalProperties: false``.
      2. Make every property that isn't already required appear as
         "required + nullable" (``type: [<old>, "null"]``). Strict
         doesn't allow partial-required; we preserve "this field is
         optional" semantics by letting the LLM emit null.
      3. Strip unsupported JSON Schema keywords from the property
         descriptors.

    Top-level non-object schemas (e.g. a tool that takes a string
    argument directly — unusual, but seen in some hand-rolled specs)
    are wrapped in a ``{ type: object, properties: {...} }`` envelope
    so OpenAI's "top-level must be object" rule is met. We don't ship
    any tools that need this today, but the wrap keeps us safe.

    Behaviour is idempotent: calling ``fixup_for_strict`` on an
    already-strict schema is a no-op modulo deep-copy cost.
    """
    if not isinstance(schema, dict):
        return schema
    out = copy.deepcopy(schema)
    _rewrite_in_place(out)
    if out.get("type") != "object":
        # Strict requires a top-level object schema. Wrap whatever we
        # got into a single-property object so the LLM still has a
        # path to express the original payload.
        return {
            "type": "object",
            "properties": {"value": out},
            "required": ["value"],
            "additionalProperties": False,
        }
    return out


def _rewrite_in_place(node: Any) -> None:
    if isinstance(node, dict):
        # Strip unsupported keywords first (so we don't recurse into
        # them needlessly).
        for kw in list(node.keys()):
            if kw in _UNSUPPORTED_KEYWORDS:
                node.pop(kw, None)

        if node.get("type") == "object":
            props = node.get("properties")
            if isinstance(props, dict):
                existing_required = set(node.get("required") or [])
                for name, prop in props.items():
                    _rewrite_in_place(prop)
                    if name not in existing_required:
                        _make_nullable(prop)
                        existing_required.add(name)
                node["required"] = sorted(existing_required)
            # additionalProperties: false is non-negotiable for strict.
            node["additionalProperties"] = False

        elif node.get("type") == "array":
            items = node.get("items")
            if isinstance(items, dict):
                _rewrite_in_place(items)
            elif isinstance(items, list):
                for it in items:
                    _rewrite_in_place(it)

        # Recurse into combinator keywords if present. These are valid
        # in strict but their sub-schemas need the same rewriting pass.
        for combinator in ("anyOf", "oneOf", "allOf"):
            if combinator in node and isinstance(node[combinator], list):
                for sub in node[combinator]:
                    _rewrite_in_place(sub)

    elif isinstance(node, list):
        for item in node:
            _rewrite_in_place(item)


def _make_nullable(prop: dict[str, Any]) -> None:
    """Add ``"null"`` to a property's type so strict mode accepts the
    LLM omitting it (by emitting null instead).

    Handles three input shapes:
      * ``{"type": "string"}``      → ``{"type": ["string", "null"]}``
      * ``{"type": ["string","integer"]}`` adds ``"null"`` to the list.
      * ``{"enum": [...]}`` without a type — leave alone; strict accepts
        bare-enum properties.
      * ``{"anyOf": [...]}`` — append ``{"type": "null"}`` to the union.
    """
    t = prop.get("type")
    if isinstance(t, str):
        if t == "null":
            return
        prop["type"] = [t, "null"]
        return
    if isinstance(t, list):
        if "null" not in t:
            t.append("null")
        return
    # Type omitted but anyOf-style union present.
    if isinstance(prop.get("anyOf"), list):
        if not any(isinstance(b, dict) and b.get("type") == "null" for b in prop["anyOf"]):
            prop["anyOf"].append({"type": "null"})
        return
    # Pure-enum property (no type) — strict accepts this as-is; we'd
    # mangle it by adding {"type": "null"}. Leave alone.
