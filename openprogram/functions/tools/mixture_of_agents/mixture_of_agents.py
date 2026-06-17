"""mixture_of_agents tool — query N models in parallel, synthesize with one.

Ports hermes-agent's MoA into OpenProgram's provider layer. Instead of
routing through OpenRouter with a single key, we dispatch to whichever
providers the user has already configured (Anthropic / OpenAI / Gemini /
etc.) via ``complete_simple`` — so each reference call uses its own key
and counts against its own quota.

Two layers, fixed:

  Layer 1 (references)  : N models answer the same prompt in parallel
  Layer 2 (aggregator)  : one model reads all N answers and synthesizes

Spec-wise the tool takes a free-form ``user_prompt`` and optional lists
of ``references`` / ``aggregator`` in ``provider:model`` form
(e.g. ``anthropic:claude-sonnet-4-6``). If unspecified, we pick from a
curated set of frontier models, silently dropping any whose provider key
isn't in the environment. If too few references succeed (< 2), we fall
back to returning the best single reference rather than erroring.

Credit: design from Wang et al., "Mixture-of-Agents Enhances Large
Language Model Capabilities" (arXiv:2406.04692), via hermes-agent's
``tools/mixture_of_agents_tool.py``. Implementation is OpenProgram-native.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..._helpers import read_string_param
from ..._runtime import function


NAME = "mixture_of_agents"

# Curated default reference set. ``provider:model_id`` strings — we pick a
# diverse lineup across Anthropic / OpenAI / Gemini so at least one survives
# even when one vendor is misbehaving.
DEFAULT_REFERENCES = [
    "anthropic:claude-sonnet-4-6",
    "openai:gpt-5",
    "google:gemini-2.5-pro",
]
DEFAULT_AGGREGATOR = "anthropic:claude-opus-4-7"

MIN_SUCCESSFUL_REFERENCES = 1  # below this we surface an error

AGGREGATOR_SYSTEM = (
    "You have been provided with a set of responses from various models to "
    "the latest user query. Your task is to synthesize these into a single, "
    "high-quality response. Critically evaluate the information, recognize "
    "that some may be biased or incorrect, and produce a refined, accurate, "
    "comprehensive reply. Do not simply replicate the given answers. Ensure "
    "the response is well-structured and coherent.\n\nResponses from models:"
)


DESCRIPTION = (
    "Route a hard problem through multiple frontier LLMs collaboratively. "
    "Fires N reference models in parallel (default 3) then synthesizes "
    "their answers with an aggregator model. Expensive — one call here "
    "costs (N+1) model calls. Use for complex math, algorithm design, or "
    "multi-step analytical reasoning where diverse perspectives help."
)


SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "user_prompt": {
                "type": "string",
                "description": "The hard question or task to route through the MoA.",
            },
            "references": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Reference models as `provider:model_id` strings "
                    "(e.g. `anthropic:claude-sonnet-4-6`). Defaults to a "
                    "curated frontier set, filtered by available API keys."
                ),
            },
            "aggregator": {
                "type": "string",
                "description": (
                    "Aggregator model as `provider:model_id`. Default is "
                    "Claude Opus; any strong synthesis model works."
                ),
            },
        },
        "required": ["user_prompt"],
    },
}


def _split(spec: str) -> tuple[str, str] | None:
    if ":" not in spec:
        return None
    provider, model_id = spec.split(":", 1)
    provider, model_id = provider.strip(), model_id.strip()
    if not provider or not model_id:
        return None
    return provider, model_id


def _env_key_for(provider: str) -> bool:
    """Cheap availability probe — provider has an env API key configured."""
    try:
        from openprogram.providers.env_api_keys import resolve_provider_key
        return bool(resolve_provider_key(provider))
    except Exception:
        return False


def _any_provider_available() -> bool:
    for spec in DEFAULT_REFERENCES + [DEFAULT_AGGREGATOR]:
        parts = _split(spec)
        if parts and _env_key_for(parts[0]):
            return True
    return False


def _tool_check_fn() -> bool:
    # At least one default provider must be reachable. Users overriding
    # ``references`` / ``aggregator`` at call time still work even if the
    # defaults are gated out, but listing the tool requires something.
    return _any_provider_available()


async def _ask_one(spec: str, user_prompt: str) -> tuple[str, str, bool]:
    """Return (spec, content_or_error, success)."""
    try:
        from openprogram.providers import (
            Context, SimpleStreamOptions, TextContent, UserMessage,
            complete_simple, get_model,
        )
    except ImportError as e:
        return (spec, f"import error: {e}", False)

    parts = _split(spec)
    if not parts:
        return (spec, f"bad spec {spec!r} (expected `provider:model`)", False)
    provider, model_id = parts

    model = get_model(provider, model_id)
    if model is None:
        return (spec, f"unknown model {provider}/{model_id}", False)
    if not _env_key_for(provider):
        return (spec, f"no API key for {provider} in env", False)

    import time
    ctx = Context(
        system_prompt="",
        messages=[UserMessage(
            role="user",
            content=[TextContent(type="text", text=user_prompt)],
            timestamp=int(time.time() * 1000),
        )],
    )
    opts = SimpleStreamOptions(max_tokens=8192)

    try:
        from openprogram.metering import usage_scope
        with usage_scope(call_kind="tool", call_label="moa:proposer"):
            resp = await complete_simple(model, ctx, opts)
    except Exception as e:
        return (spec, f"{type(e).__name__}: {e}", False)

    # Extract text from AssistantMessage.content
    parts_out: list[str] = []
    content = getattr(resp, "content", []) or []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts_out.append(block.get("text", ""))
        elif getattr(block, "type", None) == "text":
            parts_out.append(getattr(block, "text", "") or "")
    text = "\n".join(p for p in parts_out if p).strip()
    if not text:
        return (spec, "empty response", False)
    return (spec, text, True)


async def _aggregate(
    aggregator_spec: str,
    user_prompt: str,
    reference_answers: list[tuple[str, str]],
) -> str:
    """Call the aggregator with the references stitched into system prompt."""
    from openprogram.providers import (
        Context, SimpleStreamOptions, TextContent, UserMessage,
        complete_simple, get_model,
    )

    parts = _split(aggregator_spec)
    if not parts:
        return f"Error: bad aggregator spec {aggregator_spec!r}."
    provider, model_id = parts
    model = get_model(provider, model_id)
    if model is None:
        return f"Error: unknown aggregator model {provider}/{model_id}."
    if not _env_key_for(provider):
        return f"Error: no API key for aggregator provider {provider}."

    enumerated = "\n\n".join(
        f"### Model {i + 1} ({spec})\n{answer}"
        for i, (spec, answer) in enumerate(reference_answers)
    )
    system = f"{AGGREGATOR_SYSTEM}\n\n{enumerated}"

    import time
    ctx = Context(
        system_prompt=system,
        messages=[UserMessage(
            role="user",
            content=[TextContent(type="text", text=user_prompt)],
            timestamp=int(time.time() * 1000),
        )],
    )
    opts = SimpleStreamOptions(max_tokens=8192)

    try:
        from openprogram.metering import usage_scope
        with usage_scope(call_kind="tool", call_label="moa:aggregator"):
            resp = await complete_simple(model, ctx, opts)
    except Exception as e:
        return f"Error: aggregator call failed: {type(e).__name__}: {e}"

    parts_out: list[str] = []
    content = getattr(resp, "content", []) or []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts_out.append(block.get("text", ""))
        elif getattr(block, "type", None) == "text":
            parts_out.append(getattr(block, "text", "") or "")
    return "\n".join(p for p in parts_out if p).strip() or "(empty aggregator response)"


async def execute(
    user_prompt: str | None = None,
    references: list[str] | None = None,
    aggregator: str | None = None,
    **kw: Any,
) -> str:
    user_prompt = user_prompt or read_string_param(kw, "user_prompt", "prompt", "query")
    aggregator = aggregator or read_string_param(kw, "aggregator", "aggregator_model")
    if not user_prompt:
        return "Error: `user_prompt` is required."

    refs: list[str] = references or kw.get("reference_models") or list(DEFAULT_REFERENCES)
    # Drop references whose provider key isn't set — silent filter so the
    # model doesn't have to know which providers are live right now.
    filtered: list[str] = []
    for spec in refs:
        parts = _split(spec)
        if parts and _env_key_for(parts[0]):
            filtered.append(spec)
    if not filtered:
        return (
            "Error: none of the reference models are reachable. Set at least "
            "one of ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY, or "
            "pass `references=[...]` with models whose providers are set up."
        )

    agg_spec = aggregator or DEFAULT_AGGREGATOR
    # If the default aggregator isn't reachable, promote the first reachable
    # reference — better than hard-failing.
    agg_parts = _split(agg_spec)
    if not (agg_parts and _env_key_for(agg_parts[0])):
        agg_spec = filtered[0]

    results = await asyncio.gather(*[_ask_one(s, user_prompt) for s in filtered])

    successful: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    for spec, content, ok in results:
        (successful if ok else failed).append((spec, content))

    if len(successful) < MIN_SUCCESSFUL_REFERENCES:
        fail_detail = "\n".join(f"- {s}: {reason}" for s, reason in failed)
        return (
            f"Error: too few successful references "
            f"({len(successful)}/{len(filtered)}).\n\nFailures:\n{fail_detail}"
        )

    # Single-reference shortcut: skip the aggregator call, we'd be paying for
    # a rephrase of one answer.
    if len(successful) == 1:
        spec, text = successful[0]
        return (
            f"# mixture_of_agents (1 reference, skipped aggregator)\n\n"
            f"**Model**: {spec}\n\n{text}"
        )

    final = await _aggregate(agg_spec, user_prompt, successful)

    header_lines = [
        f"# mixture_of_agents",
        f"**References**: {', '.join(s for s, _ in successful)}",
        f"**Aggregator**: {agg_spec}",
    ]
    if failed:
        header_lines.append(
            "**Skipped**: " + ", ".join(f"{s} ({reason[:60]})" for s, reason in failed)
        )
    return "\n".join(header_lines) + "\n\n" + final



# Register as an AgentTool. ``execute`` stays a plain callable so any
# existing import-and-call sites keep working; the return value (an
# AgentTool) is discarded — it's already in the registry.
function(
    name=NAME,
    description=DESCRIPTION,
    parameters=SPEC["parameters"],
    toolset=['research'],
    check_fn=_tool_check_fn,
)(execute)

__all__ = ["NAME", "SPEC", "execute", "DESCRIPTION", "_tool_check_fn"]
