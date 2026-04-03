"""
runtime — LLM call interface with automatic Context integration.

When you call runtime.exec() inside an @agentic_function:

    1. READS from the Context tree:
       Calls ctx.summarize() using the function's `summarize` config
       to build context text. This text is prepended to the LLM prompt.

    2. CALLS the LLM:
       Builds a message list and calls the provided `call` function.

    3. WRITES to the Context tree:
       Records input, media, and raw_reply on the current Context node.

If called outside any @agentic_function, it works as a plain LLM call
with no context injection or recording.

Usage:
    from agentic import agentic_function, runtime

    @agentic_function
    def observe(task):
        '''Look at the screen and describe what you see.'''
        img = take_screenshot()
        return runtime.exec(
            prompt=observe.__doc__,
            input={"task": task},
            images=[img],
            call=my_llm_provider,
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
    Call an LLM and auto-record to the current Context.

    Builds a structured prompt that tells the LLM:
    - Where it is in the call tree (function name, docstring, params)
    - What happened before (ancestors + siblings' execution records)
    - What it needs to do now (prompt + input + schema)

    Args:
        prompt:   Instructions for the LLM.

        input:    Structured data to include in the prompt.
                  Serialized as JSON.

        images:   Image file paths to include.
                  Passed to the `call` function for provider-specific handling.

        context:  Override auto-generated context string.
                  If None (default): auto-generates from the Context tree.
                  If provided: used as-is, no tree query.

        schema:   Expected JSON output schema.
                  Appended as an output format constraint.

        model:    Model name or alias. Passed to the `call` function.

        call:     LLM provider function.
                  Signature: fn(prompt: str, model: str, images: list[str]) -> str
                  If None, raises NotImplementedError.

    Returns:
        str — the LLM's reply text.
    """
    ctx = _current_ctx.get(None)

    # --- Guard: one runtime.exec() per function ---
    if ctx is not None and ctx.raw_reply:
        raise RuntimeError(
            f"runtime.exec() called twice in {ctx.name}(). "
            f"Each @agentic_function should call runtime.exec() at most once. "
            f"Split into separate @agentic_function calls."
        )

    # --- Read: auto-generate context from the tree ---
    if context is None and ctx is not None:
        if ctx._summarize_kwargs:
            context = ctx.summarize(**ctx._summarize_kwargs)
        else:
            context = ctx.summarize()

    # --- Write: record what we're sending ---
    if ctx is not None:
        ctx.input = input
        ctx.media = images

    # --- Build prompt ---
    full_prompt = _build_prompt(
        context=context,
        prompt=prompt,
        input=input,
        schema=schema,
    )

    # --- Call the LLM ---
    if call is not None:
        reply = call(full_prompt, model=model, images=images or [])
    else:
        raise NotImplementedError(
            "No LLM API configured. Pass `call=your_function` to runtime.exec().\n"
            "Signature: fn(prompt: str, model: str, images: list[str]) -> str"
        )

    # --- Write: record what we got back ---
    if ctx is not None:
        ctx.raw_reply = reply

    return reply


# ======================================================================
# Prompt building
# ======================================================================

def _build_prompt(
    context: Optional[str] = None,
    prompt: str = "",
    input: Optional[dict] = None,
    schema: Optional[dict] = None,
) -> str:
    """
    Build the final prompt for the LLM.

    The context string already contains the full execution context in
    traceback format (from summarize()), including:
    - Call stack with ancestors
    - Sibling functions' name, purpose, input, output, status
    - Current function marked with <-- Current Call

    This function appends the task and output format.
    """
    sections = []

    # Execution context (already formatted by summarize)
    if context:
        sections.append(context)

    # Task
    task_parts = [f"Task: {prompt}"]
    if input:
        input_str = json.dumps(input, ensure_ascii=False, default=str, indent=2)
        task_parts.append(f"\nInput:\n{input_str}")
    sections.append("\n".join(task_parts))

    # Output format
    if schema:
        schema_str = json.dumps(schema, indent=2)
        sections.append(
            f"Output Format:\n"
            f"Return ONLY valid JSON matching this schema:\n{schema_str}"
        )

    return "\n\n".join(sections)


async def async_exec(
    prompt: str,
    input: dict = None,
    images: list[str] = None,
    context: str = None,
    schema: dict = None,
    model: str = "sonnet",
    call: Any = None,
) -> str:
    """
    Async version of exec(). Same behavior, but awaits the call function.

    The `call` function must be async:
        async fn(prompt: str, model: str, images: list[str]) -> str
    """
    ctx = _current_ctx.get(None)

    if ctx is not None and ctx.raw_reply:
        raise RuntimeError(
            f"runtime.async_exec() called twice in {ctx.name}(). "
            f"Each @agentic_function should call runtime.exec/async_exec at most once. "
            f"Split into separate @agentic_function calls."
        )

    if context is None and ctx is not None:
        if ctx._summarize_kwargs:
            context = ctx.summarize(**ctx._summarize_kwargs)
        else:
            context = ctx.summarize()

    if ctx is not None:
        ctx.input = input
        ctx.media = images

    full_prompt = _build_prompt(
        context=context,
        prompt=prompt,
        input=input,
        schema=schema,
    )

    if call is not None:
        reply = await call(full_prompt, model=model, images=images or [])
    else:
        raise NotImplementedError(
            "No async LLM API configured. Pass `call=your_async_function` to runtime.async_exec().\n"
            "Signature: async fn(prompt: str, model: str, images: list[str]) -> str"
        )

    if ctx is not None:
        ctx.raw_reply = reply

    return reply
