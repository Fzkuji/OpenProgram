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
import os
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
        self._closed = False  # Set early so __del__ is safe even if __init__ raises.
        self._prompted_functions: set[str] = set()  # Functions whose docstrings have been sent

        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")

        self._call_fn = call
        self.model = model
        self.max_retries = max_retries
        self.has_session = False  # Subclasses set True if they manage their own context
        self.on_stream = None  # Optional callback: fn(event_dict) for streaming events

    # --- Lifecycle ---

    def close(self):
        """Close this runtime: release resources, kill processes, end session.

        After close(), exec() will raise RuntimeError.
        Subclasses should override this to clean up provider-specific resources
        (kill CLI processes, clear session IDs, etc.) and call super().close().
        """
        self.has_session = False
        self._prompted_functions.clear()
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __del__(self):
        if not self._closed:
            self.close()

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
        if self._closed:
            raise RuntimeError("Runtime is closed. Create a new runtime instance.")

        # Handle plain string input
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]

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
            func_is_new = ctx.name not in self._prompted_functions

            if self.has_session:
                if func_is_new and ctx.prompt:
                    context = ctx.prompt
            else:
                kwargs = dict(ctx._summarize_kwargs) if ctx._summarize_kwargs else {}
                kwargs["prompted_functions"] = self._prompted_functions
                context = ctx.summarize(**kwargs)

            # Mark function as prompted AFTER building context
            if ctx.name:
                self._prompted_functions.add(ctx.name)

        # --- Build full content ---
        # Merge text content into the context string under an exec() marker,
        # so the LLM sees one coherent structure. Non-text content (images, etc.)
        # stays as separate blocks.
        full_content = []
        if context and ctx is not None and ctx.parent:
            # Calculate indent for exec content (one level deeper than current call)
            base = ctx._depth()
            node = ctx.parent
            while node and node.name:
                base = node._depth()
                node = node.parent
            exec_indent = "    " * (ctx._depth() - base + 1)

            # Merge text content into context
            text_parts = []
            for block in content:
                if block.get("type") == "text":
                    indented = "\n".join(
                        exec_indent + line if line.strip() else ""
                        for line in block["text"].splitlines()
                    )
                    text_parts.append(indented)
                else:
                    full_content.append(block)  # non-text goes as separate block

            if text_parts:
                merged = context + "\n" + exec_indent + "→ Current Task:\n" + "\n".join(text_parts)
            else:
                merged = context
            full_content.insert(0, {"type": "text", "text": merged})
        elif context:
            full_content.append({"type": "text", "text": context})
            full_content.extend(content)
        else:
            full_content.extend(content)

        # --- Debug: dump LLM input ---
        if os.environ.get("AGENTIC_DUMP_INPUT"):
            import json as _json
            _dump_dir = os.environ.get("AGENTIC_DUMP_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "tmp"))
            os.makedirs(_dump_dir, exist_ok=True)
            _call_path = ctx._call_path() if ctx else "unknown"
            _seq = getattr(self, '_dump_seq', 0)
            self._dump_seq = _seq + 1
            _dump_path = os.path.join(_dump_dir, f"{_seq:03d}_{_call_path}.txt")
            with open(_dump_path, "w") as _f:
                for block in full_content:
                    if block.get("type") == "text":
                        _f.write(block["text"])
                    else:
                        _f.write(_json.dumps(block, ensure_ascii=False, default=str))
                    _f.write("\n\n")
            print(f"[DUMP] {_call_path} -> {_dump_path}")

        # --- Call the LLM (with retry) ---
        attempts = ctx.attempts if ctx is not None else []
        for attempt in range(self.max_retries):
            try:
                reply = self._call(full_content, model=use_model, response_format=response_format)
                # Record successful attempt
                attempts.append({"attempt": attempt + 1, "reply": reply, "error": None})
                if ctx is not None:
                    ctx.raw_reply = reply
                return reply
            except (TypeError, NotImplementedError):
                raise  # Programming errors — don't retry
            except Exception as e:
                # Record failed attempt
                attempts.append({"attempt": attempt + 1, "reply": None, "error": f"{type(e).__name__}: {e}"})
                if attempt == self.max_retries - 1:
                    error_report = "\n".join(f"Attempt {a['attempt']}: {a['error']}" for a in attempts)
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
            if self.has_session:
                if ctx.prompt:
                    context = ctx.prompt
            else:
                if ctx._summarize_kwargs:
                    context = ctx.summarize(**ctx._summarize_kwargs)
                else:
                    context = ctx.summarize()

        full_content = []
        if context:
            full_content.append({"type": "text", "text": context})
        full_content.extend(content)

        attempts = ctx.attempts if ctx is not None else []
        for attempt in range(self.max_retries):
            try:
                reply = await self._async_call(full_content, model=use_model, response_format=response_format)
                attempts.append({"attempt": attempt + 1, "reply": reply, "error": None})
                if ctx is not None:
                    ctx.raw_reply = reply
                return reply
            except (TypeError, NotImplementedError):
                raise
            except Exception as e:
                attempts.append({"attempt": attempt + 1, "reply": None, "error": f"{type(e).__name__}: {e}"})
                if attempt == self.max_retries - 1:
                    error_report = "\n".join(f"Attempt {a['attempt']}: {a['error']}" for a in attempts)
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

    def list_models(self) -> list[str]:
        """Return available models for this runtime. Override in subclasses."""
        return [self.model] if self.model and self.model != "default" else []

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
