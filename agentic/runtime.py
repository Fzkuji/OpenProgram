"""
runtime — LLM call interface with auto Context recording.

When you call runtime.exec(), it:
1. Reads context from the tree (using the function's summarize settings)
2. Calls the LLM
3. Records input/media/raw_reply to the current Context node
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

    Args:
        prompt:   Instructions for the LLM
        input:    Structured data to include
        images:   Image file paths
        context:  Override auto-generated context. If None:
                  uses ctx.summarize(**decorator's summarize dict)
        schema:   Expected JSON output schema
        model:    Model name
        call:     LLM provider function: fn(messages, model) -> str
    """
    ctx = _current_ctx.get(None)

    # Auto-generate context from the tree
    if context is None and ctx is not None:
        if ctx._summarize_kwargs:
            context = ctx.summarize(**ctx._summarize_kwargs)
        else:
            context = ctx.summarize()

    # Record
    if ctx is not None:
        ctx.input = input
        ctx.media = images

    # Build messages and call LLM
    messages = _build_messages(prompt, input, images, context, schema)

    if call is not None:
        reply = call(messages, model=model)
    else:
        reply = _default_api_call(messages, model=model)

    if ctx is not None:
        ctx.raw_reply = reply

    return reply


def _build_messages(prompt, input=None, images=None, context=None, schema=None):
    messages = []

    if context:
        messages.append({"role": "user", "content": f"[Context]\n{context}"})
        messages.append({"role": "assistant", "content": "Understood."})

    content_parts = [{"type": "text", "text": prompt}]

    if input:
        input_str = json.dumps(input, ensure_ascii=False, default=str)
        content_parts.append({"type": "text", "text": f"\n[Input]\n{input_str}"})

    if images:
        for img_path in images:
            content_parts.append({"type": "text", "text": f"\n[Image: {img_path}]"})

    messages.append({"role": "user", "content": content_parts})

    if schema:
        schema_str = json.dumps(schema, indent=2)
        messages.append({"role": "user", "content": f"Return ONLY valid JSON matching this schema:\n{schema_str}"})

    return messages


def _default_api_call(messages, model="sonnet"):
    raise NotImplementedError(
        "No LLM API configured. Pass `call` to runtime.exec()."
    )
