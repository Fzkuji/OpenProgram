"""
agent — wraps any LLM call with automatic context recording.

Automatically:
1. Generates context summary from the current Context tree (if not provided)
2. Records input, media, and raw_reply to the current Context

Usage:
    # Wrap your own LLM call:
    reply = agent.invoke(
        call=lambda msgs: session.send(msgs),
        prompt="Look at the screen...",
        input={"task": task},
    )
    
    # Or use the convenience function:
    reply = agent.invoke(
        prompt="Look at the screen...",
        input={"task": task},
        model="sonnet",
    )
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
    Invoke an LLM and auto-record to the current Context.
    
    Args:
        prompt:   Instructions (usually the docstring)
        input:    Data to send to the LLM
        images:   Image file paths
        context:  Context summary (auto-generated from ctx.summarize() if None)
        schema:   Expected output JSON schema
        model:    Model name
        call:     Custom LLM call function: fn(messages, model) -> str
                  If None, uses default (raises NotImplementedError)
    
    Returns:
        LLM reply as string
    """
    ctx = _current_ctx.get(None)

    # Auto-generate context summary
    if context is None and ctx is not None:
        context = ctx.summarize()

    # Record input/media to Context
    if ctx is not None:
        ctx.input = input
        ctx.media = images

    # Build messages
    messages = _build_messages(prompt, input, images, context, schema)

    # Call API
    if call is not None:
        reply = call(messages, model=model)
    else:
        reply = _default_api_call(messages, model=model)

    # Record reply
    if ctx is not None:
        ctx.raw_reply = reply

    return reply


def _build_messages(
    prompt: str,
    input: dict = None,
    images: list[str] = None,
    context: str = None,
    schema: dict = None,
) -> list[dict]:
    """Build LLM API messages from the components."""
    messages = []

    # Context (previous steps' summaries)
    if context:
        messages.append({"role": "user", "content": f"[Context]\n{context}"})
        messages.append({"role": "assistant", "content": "Understood."})

    # Main prompt + input + images
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
            # In real implementation: base64 encode and add as image content

    messages.append({"role": "user", "content": content_parts})

    # Schema
    if schema:
        schema_str = json.dumps(schema, indent=2)
        messages.append({
            "role": "user",
            "content": f"Return ONLY valid JSON matching this schema:\n{schema_str}",
        })

    return messages


def _default_api_call(messages: list[dict], model: str = "sonnet") -> str:
    """
    Default API call — placeholder.
    
    In real implementation, this calls Anthropic/OpenAI/etc.
    For now, raises NotImplementedError so tests can inject _api_fn.
    """
    raise NotImplementedError(
        "No LLM API configured. Pass _api_fn to llm_call() or configure a default provider."
    )
