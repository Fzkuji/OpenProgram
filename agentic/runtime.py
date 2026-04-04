"""
runtime — LLM call interface with automatic Context integration.

Runtime is a class that wraps an LLM provider. You instantiate it once
with your provider config, then call rt.exec() inside @agentic_functions.

exec() automatically:
    1. Reads the Context tree (via summarize) to build execution context
    2. Prepends context to your content as a text block
    3. Calls _call() (override this for your provider)
    4. Records the reply to the Context tree

Usage:
    from agentic import Runtime, agentic_function

    rt = Runtime(call=my_llm_func)
    # or: subclass Runtime and override _call()

    @agentic_function
    def observe(task):
        '''Look at the screen and describe what you see.'''
        return rt.exec(content=[
            {"type": "text", "text": "Find the login button."},
            {"type": "image", "path": "screenshot.png"},
        ])
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Optional

from agentic.context import _current_ctx


class Runtime:
    """
    LLM runtime. Wraps a provider and handles Context integration.

    Two ways to use:

    1. Pass a call function:
        rt = Runtime(call=my_func, model="gpt-4o")

    2. Subclass and override _call():
        class MyRuntime(Runtime):
            def _call(self, content, response_format=None):
                # your API logic here
                return reply_text
    """

    def __init__(self, call: Optional[callable] = None, model: str = "default", max_retries: int = 2):
        """
        Args:
            call:        LLM provider function.
                         Signature: fn(content: list[dict], model: str, response_format: dict) -> str
                         If None, you must subclass and override _call().
            model:       Default model name. Passed to _call().
            max_retries: Maximum number of exec() attempts before raising.
                         Default 2 (try once, retry once on failure).
        """
        self._call_fn = call
        self.model = model
        self.max_retries = max_retries

    def exec(
        self,
        content: list[dict],
        context: Optional[str] = None,
        response_format: Optional[dict] = None,
        model: Optional[str] = None,
    ) -> str:
        """
        Call the LLM with automatic Context integration.

        Args:
            content:          List of content blocks. Each block is a dict:
                              {"type": "text", "text": "..."}
                              {"type": "image", "path": "screenshot.png"}
                              {"type": "audio", "path": "recording.wav"}
                              {"type": "file", "path": "data.csv"}

            context:          Override auto-generated context string.
                              If None: auto-generates from Context tree.

            response_format:  Expected output format (JSON schema).
                              Passed to _call() for provider-native handling.

            model:            Override the default model for this call.

        Returns:
            str — the LLM's reply text.
        """
        ctx = _current_ctx.get(None)
        use_model = model or self.model

        # --- Guard: one exec() per function ---
        if ctx is not None and ctx.raw_reply is not None:
            raise RuntimeError(
                f"exec() called twice in {ctx.name}(). "
                f"Each @agentic_function should call exec() at most once. "
                f"Split into separate @agentic_function calls."
            )

        # --- Read: auto-generate context from the tree ---
        if context is None and ctx is not None:
            if ctx._summarize_kwargs:
                context = ctx.summarize(**ctx._summarize_kwargs)
            else:
                context = ctx.summarize()

        # --- Build full content: context + user content ---
        full_content = []
        if context:
            full_content.append({"type": "text", "text": context})
        full_content.extend(content)

        # --- Call the LLM (with retry) ---
        errors = []
        for attempt in range(self.max_retries):
            try:
                reply = self._call(full_content, model=use_model, response_format=response_format)
                # --- Write: record what we got back ---
                if ctx is not None:
                    ctx.raw_reply = reply
                return reply
            except (TypeError, NotImplementedError):
                raise  # Programming errors — don't retry
            except Exception as e:
                errors.append(f"Attempt {attempt + 1}: {type(e).__name__}: {e}")
                if attempt == self.max_retries - 1:
                    # All retries exhausted
                    error_report = "\n".join(errors)
                    if ctx is not None:
                        ctx.raw_reply = None  # Keep None so error is visible
                    raise RuntimeError(
                        f"exec() failed after {self.max_retries} attempts in {ctx.name if ctx else 'unknown'}():\n{error_report}"
                    ) from e

    async def async_exec(
        self,
        content: list[dict],
        context: Optional[str] = None,
        response_format: Optional[dict] = None,
        model: Optional[str] = None,
    ) -> str:
        """Async version of exec(). Calls _async_call() instead of _call()."""
        ctx = _current_ctx.get(None)
        use_model = model or self.model

        if ctx is not None and ctx.raw_reply is not None:
            raise RuntimeError(
                f"async_exec() called twice in {ctx.name}(). "
                f"Each @agentic_function should call exec/async_exec at most once. "
                f"Split into separate @agentic_function calls."
            )

        if context is None and ctx is not None:
            if ctx._summarize_kwargs:
                context = ctx.summarize(**ctx._summarize_kwargs)
            else:
                context = ctx.summarize()

        full_content = []
        if context:
            full_content.append({"type": "text", "text": context})
        full_content.extend(content)

        errors = []
        for attempt in range(self.max_retries):
            try:
                reply = await self._async_call(full_content, model=use_model, response_format=response_format)
                if ctx is not None:
                    ctx.raw_reply = reply
                return reply
            except (TypeError, NotImplementedError):
                raise
            except Exception as e:
                errors.append(f"Attempt {attempt + 1}: {type(e).__name__}: {e}")
                if attempt == self.max_retries - 1:
                    error_report = "\n".join(errors)
                    if ctx is not None:
                        ctx.raw_reply = None
                    raise RuntimeError(
                        f"async_exec() failed after {self.max_retries} attempts in {ctx.name if ctx else 'unknown'}():\n{error_report}"
                    ) from e

    def _call(self, content: list[dict], model: str = "default", response_format: dict = None) -> str:
        """
        Call the LLM. Override this in subclasses.

        Args:
            content:          List of content blocks (text, image, audio, file).
            model:            Model name.
            response_format:  Output format constraint (JSON schema).

        Returns:
            str — the LLM's reply text.
        """
        if self._call_fn is not None:
            if inspect.iscoroutinefunction(self._call_fn):
                raise TypeError(
                    "exec() received an async call function. "
                    "Use async_exec() for async providers, or pass a sync function."
                )
            result = self._call_fn(content, model=model, response_format=response_format)
            if asyncio.iscoroutine(result):
                raise TypeError(
                    "call function returned a coroutine. "
                    "Use async_exec() for async providers, or pass a sync function."
                )
            return result
        raise NotImplementedError(
            "No LLM provider configured. Either pass `call=your_function` to Runtime(), "
            "or subclass Runtime and override _call()."
        )

    async def _async_call(self, content: list[dict], model: str = "default", response_format: dict = None) -> str:
        """Async version of _call(). Override for async providers."""
        if self._call_fn is not None:
            result = self._call_fn(content, model=model, response_format=response_format)
            if asyncio.iscoroutine(result):
                return await result
            # Sync function passed to async_exec — just return it
            return result
        raise NotImplementedError(
            "No async LLM provider configured. Either pass an async `call` to Runtime(), "
            "or subclass Runtime and override _async_call()."
        )
