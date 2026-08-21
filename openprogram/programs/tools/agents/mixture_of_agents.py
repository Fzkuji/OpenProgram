"""mixture_of_agents tool — query N models in parallel, synthesize with one.

Ports hermes-agent's MoA into OpenProgram's provider layer. hermes routes
everything through OpenRouter with a hardcoded frontier lineup; we instead
pick defaults from the local model registry (``openprogram.providers.
get_models()``) — whatever subscription/API providers this install actually
has — so the default call path always targets reachable models.

Two layers, fixed:

  Layer 1 (references)  : N models answer the same prompt in parallel
  Layer 2 (aggregator)  : one model reads all N answers and synthesizes

Default selection: group the registry by provider, drop the providers with no
working credential, take the strongest-looking model per surviving provider
(keyword rank over the model id), and use up to ``MAX_DEFAULT_REFERENCES``
distinct providers as references. The aggregator is the pick from the next
unused provider, falling back to the first reference. With a single successful
reference the aggregator is skipped entirely.

Explicit ``references=["provider:model_id"]`` / ``aggregator="provider:model"``
still override everything; unknown specs error with the list of available
``provider:model`` pairs from the registry.

Credit: design from Wang et al., "Mixture-of-Agents Enhances Large
Language Model Capabilities" (arXiv:2406.04692), via hermes-agent's
``tools/mixture_of_agents_tool.py``. Implementation is OpenProgram-native.
"""

from __future__ import annotations

import asyncio
from typing import Any

from openprogram.programs._helpers import read_string_param
from openprogram.programs._runtime import function


NAME = "mixture_of_agents"

MAX_DEFAULT_REFERENCES = 3
MIN_SUCCESSFUL_REFERENCES = 1  # below this we surface an error

# From the MoA paper via hermes: diverse sampling for references, focused
# synthesis for the aggregator.
REFERENCE_TEMPERATURE = 0.6
AGGREGATOR_TEMPERATURE = 0.4

# One retry per reference call (hermes uses 6 attempts with exponential
# backoff against OpenRouter rate limits; our subscription providers fail
# hard or succeed, so one short-backoff retry covers the transient cases).
REFERENCE_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 2

# Rough strength ranking over model-id keywords: earlier keyword wins when a
# provider offers several models. ponytail: naive keyword rank, replace with
# registry metadata if models ever carry a capability tier.
STRENGTH_KEYWORDS = ("opus", "terra", "pro", "max", "plus", "sol")

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
    "Fires N reference models in parallel (default up to 3, one per "
    "configured provider) then synthesizes their answers with an aggregator "
    "model. Expensive — one call here costs (N+1) model calls. Use for "
    "complex math, algorithm design, or multi-step analytical reasoning "
    "where diverse perspectives help."
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
                    "Reference models as `provider:model_id` strings. "
                    "Defaults to the strongest model from each configured "
                    "provider in the local registry (up to 3 providers)."
                ),
            },
            "aggregator": {
                "type": "string",
                "description": (
                    "Aggregator model as `provider:model_id`. Defaults to a "
                    "registry model from a provider not used as a reference."
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


def _strength_rank(model_id: str) -> int:
    lowered = model_id.lower()
    for i, kw in enumerate(STRENGTH_KEYWORDS):
        if kw in lowered:
            return i
    return len(STRENGTH_KEYWORDS)


def _registry_specs() -> list[str]:
    """All registry models as sorted ``provider:model_id`` strings."""
    from openprogram.providers import get_models
    return sorted(f"{m.provider}:{m.id}" for m in get_models())


def _usable_providers() -> set[str]:
    """Registry providers that actually have a working credential.

    A provider can be enabled (models listed, picker shows them) with no
    credential stored — every call to it fails at auth. Reuses the model
    picker's own signal (``metadata.is_configured``: auth store, CLI binary,
    OAuth file, env key) so the default lineup never burns one of its three
    reference slots on a guaranteed failure.
    """
    from openprogram.providers import get_models
    from openprogram.providers.metadata import is_configured
    return {p for p in {m.provider for m in get_models()} if is_configured(p)}


def _pick_defaults() -> tuple[list[str], str | None]:
    """Pick default (references, aggregator) from the model registry.

    One model per credentialed provider (strongest by keyword rank, registry
    order as tiebreak), providers in registry order. References take the
    first ``MAX_DEFAULT_REFERENCES`` providers; the aggregator takes the next
    unused provider's pick, falling back to the first reference.
    """
    from openprogram.providers import get_models

    usable = _usable_providers()
    best_per_provider: dict[str, Any] = {}  # provider -> Model, registry order
    for m in get_models():
        if m.provider not in usable:
            continue
        cur = best_per_provider.get(m.provider)
        if cur is None or _strength_rank(m.id) < _strength_rank(cur.id):
            best_per_provider[m.provider] = m

    picks = [f"{m.provider}:{m.id}" for m in best_per_provider.values()]
    references = picks[:MAX_DEFAULT_REFERENCES]
    if not references:
        return [], None
    aggregator = picks[MAX_DEFAULT_REFERENCES] if len(picks) > MAX_DEFAULT_REFERENCES else references[0]
    return references, aggregator


def _tool_check_fn() -> bool:
    # MoA needs diverse perspectives: at least two distinct reachable
    # providers. A single provider still works via explicit `references`, but
    # then the tool adds nothing over a plain model call.
    try:
        return len(_usable_providers()) >= 2
    except Exception:
        return False


def _extract_text(resp: Any) -> str:
    parts: list[str] = []
    content = getattr(resp, "content", []) or []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "\n".join(p for p in parts if p).strip()


async def _call_model(
    spec: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    label: str,
    attempts: int = 1,
) -> tuple[str, bool]:
    """Call one registry model. Returns (text_or_error, success)."""
    try:
        from openprogram.providers import (
            Context, SimpleStreamOptions, TextContent, UserMessage,
            complete_simple, get_model,
        )
    except ImportError as e:
        return (f"import error: {e}", False)

    parts = _split(spec)
    if not parts:
        return (f"bad spec {spec!r} (expected `provider:model`)", False)
    provider, model_id = parts

    model = get_model(provider, model_id)
    if model is None:
        return (f"unknown model {provider}/{model_id}", False)

    import time
    ctx = Context(
        system_prompt=system_prompt,
        messages=[UserMessage(
            role="user",
            content=[TextContent(type="text", text=user_prompt)],
            timestamp=int(time.time() * 1000),
        )],
    )
    opts = SimpleStreamOptions(max_tokens=8192, temperature=temperature)

    last_error = "empty response"
    for attempt in range(attempts):
        if attempt > 0:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS)
        try:
            from openprogram.usage import usage_scope
            with usage_scope(call_kind="tool", call_label=label):
                resp = await complete_simple(model, ctx, opts)
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            continue
        text = _extract_text(resp)
        if text:
            return (text, True)
        last_error = "empty response"
    return (last_error, False)


async def _ask_one(spec: str, user_prompt: str) -> tuple[str, str, bool]:
    """Return (spec, content_or_error, success)."""
    text, ok = await _call_model(
        spec, "", user_prompt, REFERENCE_TEMPERATURE,
        label="moa:proposer", attempts=REFERENCE_ATTEMPTS,
    )
    return (spec, text, ok)


async def _aggregate(
    aggregator_spec: str,
    user_prompt: str,
    reference_answers: list[tuple[str, str]],
) -> str:
    """Call the aggregator with the references stitched into the system prompt."""
    enumerated = "\n\n".join(
        f"### Model {i + 1} ({spec})\n{answer}"
        for i, (spec, answer) in enumerate(reference_answers)
    )
    system = f"{AGGREGATOR_SYSTEM}\n\n{enumerated}"
    text, ok = await _call_model(
        aggregator_spec, system, user_prompt, AGGREGATOR_TEMPERATURE,
        label="moa:aggregator",
    )
    if not ok:
        return f"Error: aggregator call failed: {text}"
    return text


def _unknown_spec_error(bad: list[str]) -> str:
    available = "\n".join(f"- {s}" for s in _registry_specs())
    return (
        f"Error: unknown model spec(s): {', '.join(bad)}.\n"
        f"Use `provider:model_id` from the local registry:\n{available}"
    )


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

    from openprogram.providers import get_model

    explicit_refs = references or kw.get("reference_models")
    if explicit_refs:
        refs = list(dict.fromkeys(explicit_refs))  # dedupe, keep order
        bad = [s for s in refs
               if not (p := _split(s)) or get_model(*p) is None]
        if bad:
            return _unknown_spec_error(bad)
        agg_spec = aggregator
    else:
        refs, default_agg = _pick_defaults()
        if not refs:
            return (
                "Error: the model registry is empty — configure at least one "
                "provider, or pass `references=[...]`."
            )
        agg_spec = aggregator or default_agg

    if aggregator:
        parts = _split(aggregator)
        if not parts or get_model(*parts) is None:
            return _unknown_spec_error([aggregator])
    if not agg_spec:
        agg_spec = refs[0]

    results = await asyncio.gather(*[_ask_one(s, user_prompt) for s in refs])

    successful: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    for spec, content, ok in results:
        (successful if ok else failed).append((spec, content))

    if len(successful) < MIN_SUCCESSFUL_REFERENCES:
        fail_detail = "\n".join(f"- {s}: {reason}" for s, reason in failed)
        available = "\n".join(f"- {s}" for s in _registry_specs())
        return (
            f"Error: too few successful references "
            f"({len(successful)}/{len(refs)}).\n\nFailures:\n{fail_detail}\n\n"
            f"Available `provider:model` specs:\n{available}"
        )

    # Why the lineup shrank — reported on BOTH paths, so "I only got one
    # answer" always comes with the reason the others dropped out.
    skipped = ["**Skipped**: " + ", ".join(
        f"{s} ({reason[:60]})" for s, reason in failed)] if failed else []

    # Single-reference shortcut: skip the aggregator call, we'd be paying for
    # a rephrase of one answer.
    if len(successful) == 1:
        spec, text = successful[0]
        header_lines = [
            "# mixture_of_agents (1 reference, skipped aggregator)",
            f"**Model**: {spec}",
            *skipped,
        ]
        return "\n".join(header_lines) + "\n\n" + text

    final = await _aggregate(agg_spec, user_prompt, successful)

    header_lines = [
        f"# mixture_of_agents",
        f"**References**: {', '.join(s for s, _ in successful)}",
        f"**Aggregator**: {agg_spec}",
        *skipped,
    ]
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
