"""Tool-schema normalization across providers — the unified layer that
translates one canonical tool schema into the dialect each target API
accepts.

Every provider takes a tool's *canonical* JSON Schema
(``Tool.parameters``) and adapts it into its own wire format
(OpenAI ``parameters``+``strict``, Anthropic ``input_schema``, Gemini
``parameters``/``parametersJsonSchema``, Bedrock ``inputSchema.json``).
The *wrapping* lives in each provider; the *schema dialect* — the
subset of JSON Schema each API actually accepts — is owned here, in one
place, so support differences live at a single edit point.

Two facts shape the API of this module:

  1. **Support is per-(api, model), not per-api.** OpenAI's strict
     mode is always available; Anthropic's strict mode exists only on
     recent models (Sonnet 4.5+ / Opus 4.1+) and behind a beta header;
     Gemini has no strict flag at all. So the picker takes ``model_id``.

  2. **Unsupported degrades to passthrough.** A model/API that can't do
     a given constraint gets the ``passthrough`` dialect — canonical
     schema sent unchanged, no flag, no header. We never force a
     constraint an endpoint can't accept.

Public surface:

  * ``dialect_for(api, model_id=None) -> DialectName`` — model-aware
    picker. The one place that knows who supports strict.
  * ``normalize(schema, dialect) -> schema`` — THE transformer.
  * ``normalize_for(api, schema, model_id=None)`` — sugar:
    ``normalize(schema, dialect_for(api, model_id))``.
  * ``wants_strict_flag(api, model_id=None) -> bool`` — whether the
    provider should also set ``"strict": true`` on the tool (OpenAI
    family always; Anthropic on supported models). Env-gated.
  * ``anthropic_supports_strict(model_id) -> bool`` — exposed so the
    Anthropic provider can decide whether to send the beta header.
  * ``strict_tools_enabled()`` — the ``OPENPROGRAM_STRICT_TOOLS`` toggle.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from . import dialects

DialectName = Literal["openai_strict", "gemini_openapi", "passthrough"]


class SchemaNormalizationError(RuntimeError):
    """A dialect transform failed on a tool schema. Raised (not
    swallowed) so a malformed schema surfaces loudly rather than being
    sent raw — raw-into-strict / raw-into-Gemini 400s anyway, and
    silent raw would drop the constrained-decoding guarantee the user
    opted into. The ``OPENPROGRAM_STRICT_TOOLS=0`` env kill-switch is
    the deliberate safe fallback."""


# name → transform fn
_DIALECTS: dict[str, Any] = {
    "openai_strict": dialects.openai_strict,
    "gemini_openapi": dialects.gemini_openapi,
    "passthrough": dialects.passthrough,
}

# APIs whose tool calls go through OpenAI's strict structured-outputs
# path. Single source feeding both the dialect picker and the
# ``"strict": true`` flag. Anthropic is NOT here — its strict support
# is model-gated, handled separately in ``dialect_for``.
_OPENAI_STRICT_APIS: frozenset[str] = frozenset({
    "openai-completions",
    "openai-responses",
    "azure-openai-responses",
    "openai-codex",
})

# The Anthropic Messages API id (strict support is per-model, below).
_ANTHROPIC_API = "anthropic-messages"


def strict_tools_enabled() -> bool:
    """The ``OPENPROGRAM_STRICT_TOOLS`` default + opt-out, single source.

    Defaults ON — both OpenAI and Anthropic recommend strict mode; the
    grammar-constrained guarantee (the model literally cannot emit a
    value outside an enum, an extra field, or a wrong type) is the
    point. Set ``OPENPROGRAM_STRICT_TOOLS=0`` to fall back to
    passthrough everywhere if a schema ever hits a transform edge case.
    """
    return os.environ.get("OPENPROGRAM_STRICT_TOOLS", "1").strip().lower() not in (
        "0", "false", "no", "off", "",
    )


def anthropic_supports_strict(model_id: str | None) -> bool:
    """Whether an Anthropic model supports strict tool use.

    Anthropic shipped strict tool use (the ``structured-outputs-2025-11-13``
    beta) for Claude Sonnet 4.5+ / Opus 4.1+ / Haiku 4.5+. Older models
    (claude-3-x, sonnet-4-0) don't support it — sending the beta header
    to them errors, so we must version-gate. Conservative: anything we
    can't confidently parse as new enough returns False (→ passthrough).
    """
    if not model_id:
        return False
    mid = model_id.lower()
    # Match ``claude-<family>-<major>-<minor>`` (the post-4 naming).
    # Old ids like ``claude-3-5-sonnet-20241022`` put the version
    # *before* the family and won't match this — correctly rejected.
    import re
    m = re.search(r"(sonnet|opus|haiku)-(\d+)-(\d+)", mid)
    if not m:
        return False
    family, major_s, minor_s = m.group(1), m.group(2), m.group(3)
    major, minor = int(major_s), int(minor_s)
    if major > 4:
        return True
    if major < 4:
        return False
    # major == 4: per-family minimum minor
    floor = {"sonnet": 5, "opus": 1, "haiku": 5}.get(family, 99)
    return minor >= floor


def dialect_for(api: str | None, model_id: str | None = None) -> DialectName:
    """Pick the schema dialect for ``(api, model_id)``.

    Returns ``"openai_strict"`` for the OpenAI family and for
    strict-capable Anthropic models (when the env toggle is on),
    otherwise ``"passthrough"``. Gemini's two modes
    (``gemini_openapi`` vs ``passthrough``) are chosen at the call site
    via an explicit ``normalize(schema, "gemini_openapi")`` because the
    choice is request-time (``use_parameters``), not derivable from the
    api id alone.
    """
    if not api:
        return "passthrough"
    if strict_tools_enabled():
        if api in _OPENAI_STRICT_APIS:
            return "openai_strict"
        if api == _ANTHROPIC_API and anthropic_supports_strict(model_id):
            return "openai_strict"
    return "passthrough"


def wants_strict_flag(api: str | None, model_id: str | None = None) -> bool:
    """Whether the provider should also set ``"strict": true`` on the
    tool wrapper. True exactly when ``dialect_for`` chose
    ``openai_strict`` — i.e. the schema is strict-shaped and the API
    honours the flag. Kept as a separate function (not just an equality
    check at call sites) so a future "strict-shaped schema but
    strict:false" need is a one-line change here."""
    return dialect_for(api, model_id) == "openai_strict"


def normalize(schema: Any, dialect: DialectName) -> Any:
    """Apply a named dialect transform to a canonical tool schema.

    Non-dict schemas pass through untouched. A transform that raises is
    re-raised as ``SchemaNormalizationError`` (never swallowed →
    never silently sent raw)."""
    if not isinstance(schema, dict):
        return schema
    fn = _DIALECTS.get(dialect)
    if fn is None:
        raise SchemaNormalizationError(f"unknown dialect {dialect!r}")
    try:
        return fn(schema)
    except Exception as e:  # pragma: no cover — defensive
        raise SchemaNormalizationError(
            f"dialect {dialect!r} failed: {type(e).__name__}: {e}"
        ) from e


def normalize_for(api: str | None, schema: Any, model_id: str | None = None) -> Any:
    """Sugar: ``normalize(schema, dialect_for(api, model_id))``."""
    return normalize(schema, dialect_for(api, model_id))


__all__ = [
    "DialectName",
    "SchemaNormalizationError",
    "dialect_for",
    "normalize",
    "normalize_for",
    "wants_strict_flag",
    "anthropic_supports_strict",
    "strict_tools_enabled",
]
