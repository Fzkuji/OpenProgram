"""Tool-schema normalization across providers.

Every provider takes a tool's *canonical* JSON Schema
(``Tool.parameters``) and adapts it into whatever its wire format
demands:

    OpenAI Completions   {"function": {"parameters": <schema>, "strict": …}}
    OpenAI Responses     {"parameters": <schema>, "strict": …}
    Anthropic            {"input_schema": <schema>}
    Google               {"parametersJsonSchema": <schema>}
    Bedrock              {"inputSchema": {"json": <schema>}}

Most of that wrapping is trivial. The non-trivial part — and the
reason this is its own package rather than a helper buried next to one
provider — is **schema dialect normalization**: a given API only
accepts a constrained subset of JSON Schema, and the exact subset
differs per API. OpenAI's "strict mode" is the strictest (and the most
valuable, because it turns the schema into a hard constrained-decoding
contract). Future dialects (Gemini's limited ``parameters`` form, etc.)
get their own module here alongside ``strict.py``.

Public surface:

  * ``strict_tools_enabled()`` — the single source of the
    ``OPENPROGRAM_STRICT_TOOLS`` env toggle. Was previously duplicated
    in two provider files; centralised here so the default + opt-out
    semantics can't drift.
  * ``api_wants_strict(api)`` — whether an API id uses OpenAI strict
    mode. The one place that knows the membership list.
  * ``normalize_parameters(api, schema)`` — the dispatcher every
    provider calls. Returns a schema fitted to ``api``'s dialect (or
    the schema unchanged when no normalization applies). Providers
    don't need to know *which* transform ran.
  * ``fixup_for_strict(schema)`` — the OpenAI strict rewriter itself,
    re-exported for callers that already know they want strict.
"""

from __future__ import annotations

import os
from typing import Any

from .strict import fixup_for_strict


# API ids whose tool definitions go through OpenAI's strict-mode
# structured-outputs path. These are the wire formats that accept
# (and benefit from) ``"strict": true`` + the constrained JSON Schema
# subset. Anthropic / Google / Bedrock validate tool schemas their own
# way and are left untouched by ``normalize_parameters``.
_OPENAI_STRICT_APIS: frozenset[str] = frozenset({
    "openai-completions",
    "openai-responses",
    "azure-openai-responses",
    "openai-codex",
})


def strict_tools_enabled() -> bool:
    """Single source of truth for the strict-mode default + opt-out.

    Defaults to ON — OpenAI's docs explicitly recommend always
    enabling strict mode; the constrained-decoding guarantee (the
    model literally cannot emit a value outside an enum, an extra
    field, or a wrong type) is too valuable to leave off. Set
    ``OPENPROGRAM_STRICT_TOOLS=0`` (or false/no/off) to fall back to
    the lax path if a tool schema ever hits a fixup edge case in the
    wild.
    """
    return os.environ.get("OPENPROGRAM_STRICT_TOOLS", "1").strip().lower() not in (
        "0", "false", "no", "off", "",
    )


def api_wants_strict(api: str | None) -> bool:
    """Whether tool calls for ``api`` should be sent in strict mode.

    True only when the API is in the OpenAI-strict family AND the env
    toggle is on. Centralises both checks so providers ask one
    question instead of re-deriving the rule.
    """
    if not api:
        return False
    return api in _OPENAI_STRICT_APIS and strict_tools_enabled()


def normalize_parameters(api: str | None, schema: Any) -> Any:
    """Normalize a canonical tool ``parameters`` schema for ``api``.

    The single entry point providers call. Today it applies the
    OpenAI strict rewrite for the strict-family APIs and returns the
    schema unchanged for everyone else; adding a new dialect (e.g. a
    Gemini-specific cleanup) means one new branch here and a sibling
    module — no churn at the call sites.
    """
    if not isinstance(schema, dict):
        return schema
    if api_wants_strict(api):
        return fixup_for_strict(schema)
    return schema


__all__ = [
    "strict_tools_enabled",
    "api_wants_strict",
    "normalize_parameters",
    "fixup_for_strict",
]
