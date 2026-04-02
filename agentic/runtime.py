"""
runtime — Agentic Runtime interface.

This module wraps LLM calls with automatic Context recording.
When you call runtime.exec(), two things happen automatically:
1. Context summary is generated and injected (if not provided)
2. Input, media, and reply are recorded to the current Context node

The name "runtime" comes from the dual-runtime concept:
- Python Runtime: deterministic code (OCR, click, file I/O)
- Agentic Runtime: LLM reasoning (this module)

Together they form the Agentic Function execution model.

Usage:
    from agentic import runtime

    # With custom LLM provider:
    reply = runtime.exec(
        prompt="Look at the screen...",
        input={"task": task},
        call=lambda msgs, model: my_api(msgs),
    )
    
    # With default provider (must be configured):
    reply = runtime.exec(
        prompt="Look at the screen...",
        input={"task": task},
        model="sonnet",
    )

Design decisions (lessons learned):

    1. Naming journey: llm_call → invoke → run → exec → execute → exec
       We settled on exec() after looking at Java's Runtime.exec().
       Yes, exec is a Python builtin, but as a method on the runtime module
       it doesn't conflict: `runtime.exec()` is unambiguous.
       You CAN'T do `from agentic import exec` though — use `from agentic import runtime`.

    2. Module-level function vs class:
       We debated Runtime class (like Java) vs module function (like Python's subprocess).
       Python convention: use classes only when you need state.
       We chose module function because:
       - No persistent state needed between calls
       - model/call can be passed per-call
       - Simpler API surface
       
       If state is needed later (e.g. connection pooling, retry config),
       we can add a Runtime class that backs the module function.

    3. The `call` parameter is a provider injection hook.
       It's typed as Any (should be Callable) — known weakness.
       The framework builds messages in its own format, then the call function
       must understand that format. This is leaky but pragmatic for now.
       TODO: Define a proper Provider protocol with structured request/response.

    4. _build_messages() has hardcoded prompt engineering (fake "Understood."
       assistant message). This is scaffolding, not a stable abstraction.
       A real implementation should separate message building from provider.

    5. input/media/raw_reply recording overwrites previous values.
       If a function calls runtime.exec() twice, only the last call is recorded.
       TODO: Change Context to use llm_calls: list[LLMCall].
"""

from __future__ import annotations

import json
from typing import Any, Optional

from agentic.context import _current_ctx


def exec(
    prompt: str,
    input: dict = None,
    images: list[str] = None,
    context: str = None,
    schema: dict = None,
    model: str = "sonnet",
    call: Any = None,
) -> str:
    """
    Call an LLM and auto-record to the current Context.
    
    This is the Agentic Runtime's execution interface — the LLM equivalent
    of calling a Python function. The framework handles context injection
    and recording automatically.
    
    Args:
        prompt:   Instructions for the LLM (usually the function's docstring)
        input:    Structured data to include in the LLM prompt
        images:   Image file paths to include (screenshots, diagrams, etc.)
        context:  Pre-built context string. If None, auto-generated from
                  ctx.summarize() using the current Context tree.
        schema:   Expected JSON output schema (for structured output)
        model:    Model name/alias (e.g. "sonnet", "gpt-4o")
        call:     Custom LLM call function: fn(messages, model) -> str
                  Lets you plug in any provider (Anthropic, OpenAI, local, etc.)
                  If None, uses _default_api_call (raises NotImplementedError)
    
    Returns:
        LLM reply as string
    
    Side effects:
        - Records input, images, raw_reply to current Context (if exists)
        - Auto-generates context summary from tree (if context not provided)
    """
    ctx = _current_ctx.get(None)

    # --- Auto-generate context from the tree ---
    # This is where the "tree records everything, summarize queries selectively" 
    # principle comes to life.
    #
    # Priority:
    #   1. Explicit `context` string passed to exec() → use as-is
    #   2. ContextPolicy on the @agentic_function → policy.apply(ctx)
    #   3. Default → ctx.summarize() (all ancestors + all siblings)
    #
    # ContextPolicy handles: depth, siblings, decay, progressive detail,
    # cache stability, token budget, path filtering.
    if context is None and ctx is not None:
        if ctx._policy is not None:
            context = ctx._policy.apply(ctx)
        else:
            context = ctx.summarize()

    # --- Record what we're sending to the LLM ---
    # NOTE: This overwrites previous values if exec() is called multiple times
    # in the same function. Known limitation — see module docstring.
    if ctx is not None:
        ctx.input = input
        ctx.media = images

    # --- Build LLM messages ---
    messages = _build_messages(prompt, input, images, context, schema)

    # --- Call the LLM ---
    if call is not None:
        reply = call(messages, model=model)
    else:
        reply = _default_api_call(messages, model=model)

    # --- Record what the LLM replied ---
    if ctx is not None:
        ctx.raw_reply = reply

    return reply


# ======================================================================
# Message building (internal, provider-agnostic-ish)
# ======================================================================

def _build_messages(
    prompt: str,
    input: dict = None,
    images: list[str] = None,
    context: str = None,
    schema: dict = None,
) -> list[dict]:
    """
    Build LLM API messages from the components.
    
    WARNING: This is scaffolding, not a stable abstraction.
    The fake "Understood." assistant message is hardcoded prompt engineering.
    A real implementation should have provider-specific message builders.
    """
    messages = []

    # Context (previous steps' summaries) — injected as a preamble
    if context:
        messages.append({"role": "user", "content": f"[Context]\n{context}"})
        messages.append({"role": "assistant", "content": "Understood."})

    # Main prompt + input data + images
    content_parts = [{"type": "text", "text": prompt}]

    if input:
        input_str = json.dumps(input, ensure_ascii=False, default=str)
        content_parts.append({"type": "text", "text": f"\n[Input]\n{input_str}"})

    if images:
        for img_path in images:
            content_parts.append({
                "type": "text",
                "text": f"\n[Image: {img_path}]",
            })
            # TODO: In real implementation, base64 encode and add as image_url content

    messages.append({"role": "user", "content": content_parts})

    # Schema hint for structured output
    if schema:
        schema_str = json.dumps(schema, indent=2)
        messages.append({
            "role": "user",
            "content": f"Return ONLY valid JSON matching this schema:\n{schema_str}",
        })

    return messages


def _default_api_call(messages: list[dict], model: str = "sonnet") -> str:
    """
    Placeholder — no LLM provider configured.
    
    Pass the `call` parameter to runtime.exec() to use your own provider:
        runtime.exec(prompt=..., call=lambda msgs, model: my_api(msgs))
    """
    raise NotImplementedError(
        "No LLM API configured. Pass `call` to runtime.exec() to use your own provider, "
        "e.g.: runtime.exec(prompt=..., call=lambda msgs, model: my_api(msgs))"
    )
