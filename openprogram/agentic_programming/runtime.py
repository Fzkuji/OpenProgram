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
    from openprogram import Runtime, agentic_function

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

from openprogram.agentic_programming.context import Context, _current_ctx
from openprogram.agentic_programming.events import _emit_event


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
        self.last_usage = None  # Last call's token usage: {input_tokens, output_tokens, ...}
        self.usage_is_cumulative = False  # True if last_usage accumulates across calls (e.g. Codex CLI)

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
        Call the LLM. Creates an exec node in the Context tree.

        Args:
            content:          List of content blocks. Each block is a dict:
                              {"type": "text", "text": "..."}
                              {"type": "image", "path": "screenshot.png"}
                              {"type": "audio", "path": "recording.wav"}
                              {"type": "file", "path": "data.csv"}

            context:          Override auto-generated context string.
                              If None: exec node calls summarize() on itself.

            response_format:  Expected output format (JSON schema).
                              Passed to _call() for provider-native handling.

            model:            Override the default model for this call.

        Returns:
            str — the LLM's reply text.
        """
        if self._closed:
            raise RuntimeError("Runtime is closed. Create a new runtime instance.")

        # Cancel check — lets long-running loops inside one function also abort.
        from openprogram.agentic_programming.function import _run_pre_invocation_hooks
        _run_pre_invocation_hooks()

        # Handle plain string input
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]

        import time as _time
        parent_ctx = _current_ctx.get(None)
        use_model = model or self.model
        content_text = "\n".join(b["text"] for b in content if b.get("type") == "text")

        # --- Create exec child node ---
        exec_ctx = None
        if parent_ctx is not None:
            exec_ctx = Context(
                name="_exec",
                node_type="exec",
                params={"_content": content_text},
                parent=parent_ctx,
                start_time=_time.time(),
                render="result",
            )
            parent_ctx.children.append(exec_ctx)
            _emit_event("node_created", exec_ctx)

        # --- Context: exec node summarizes itself ---
        if context is None and exec_ctx is not None:
            if self.has_session:
                # Session providers manage their own context
                if parent_ctx.prompt and parent_ctx.name not in self._prompted_functions:
                    context = parent_ctx.prompt
                    self._prompted_functions.add(parent_ctx.name)
            else:
                # Use parent's summarize config
                kwargs = dict(parent_ctx._summarize_kwargs) if parent_ctx._summarize_kwargs else {}
                kwargs["prompted_functions"] = self._prompted_functions
                context = exec_ctx.summarize(**kwargs)

        # --- Merge content into context ---
        full_content = _merge_content(context, content, exec_ctx)

        # --- System prompt: walk up for nearest @agentic_function(system=...) ---
        system_text = _find_system_prompt(parent_ctx)
        if system_text:
            full_content.insert(0, {"type": "text", "text": system_text, "role": "system"})

        # --- Debug: dump LLM input ---
        if os.environ.get("AGENTIC_DUMP_INPUT"):
            import json as _json
            _dump_dir = os.environ.get("AGENTIC_DUMP_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "tmp"))
            os.makedirs(_dump_dir, exist_ok=True)
            _call_path = exec_ctx._call_path() if exec_ctx else (parent_ctx._call_path() if parent_ctx else "unknown")
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
        attempts = exec_ctx.attempts if exec_ctx is not None else []
        for attempt in range(self.max_retries):
            try:
                reply = self._call(full_content, model=use_model, response_format=response_format)
                attempts.append({"attempt": attempt + 1, "reply": reply, "error": None})
                if exec_ctx is not None:
                    exec_ctx.raw_reply = reply
                    exec_ctx.output = reply
                    exec_ctx.status = "success"
                    exec_ctx.end_time = _time.time()
                    _emit_event("node_completed", exec_ctx)
                    # Backward compat: parent function also gets latest reply
                    parent_ctx.raw_reply = reply
                return reply
            except (TypeError, NotImplementedError):
                raise  # Programming errors — don't retry
            except Exception as e:
                attempts.append({"attempt": attempt + 1, "reply": None, "error": f"{type(e).__name__}: {e}"})
                if attempt == self.max_retries - 1:
                    if exec_ctx is not None:
                        exec_ctx.error = str(e)
                        exec_ctx.status = "error"
                        exec_ctx.end_time = _time.time()
                        _emit_event("node_completed", exec_ctx)
                    error_report = "\n".join(f"Attempt {a['attempt']}: {a['error']}" for a in attempts)
                    raise RuntimeError(
                        f"exec() failed after {self.max_retries} attempts in {parent_ctx.name if parent_ctx else 'unknown'}():\n{error_report}"
                    ) from e

    async def async_exec(
        self,
        content: list[dict],
        context: Optional[str] = None,
        response_format: Optional[dict] = None,
        model: Optional[str] = None,
    ) -> str:
        """Async version of exec(). Creates exec node, calls _async_call()."""
        # Cancel check — lets long-running loops inside one function also abort.
        from openprogram.agentic_programming.function import _run_pre_invocation_hooks
        _run_pre_invocation_hooks()

        if isinstance(content, str):
            content = [{"type": "text", "text": content}]

        import time as _time
        parent_ctx = _current_ctx.get(None)
        use_model = model or self.model
        content_text = "\n".join(b["text"] for b in content if b.get("type") == "text")

        # --- Create exec child node ---
        exec_ctx = None
        if parent_ctx is not None:
            exec_ctx = Context(
                name="_exec",
                node_type="exec",
                params={"_content": content_text},
                parent=parent_ctx,
                start_time=_time.time(),
                render="result",
            )
            parent_ctx.children.append(exec_ctx)
            _emit_event("node_created", exec_ctx)

        # --- Context: exec node summarizes itself ---
        if context is None and exec_ctx is not None:
            if self.has_session:
                if parent_ctx.prompt and parent_ctx.name not in self._prompted_functions:
                    context = parent_ctx.prompt
                    self._prompted_functions.add(parent_ctx.name)
            else:
                kwargs = dict(parent_ctx._summarize_kwargs) if parent_ctx._summarize_kwargs else {}
                kwargs["prompted_functions"] = self._prompted_functions
                context = exec_ctx.summarize(**kwargs)

        # --- Merge content into context ---
        full_content = _merge_content(context, content, exec_ctx)

        # --- System prompt: walk up for nearest @agentic_function(system=...) ---
        system_text = _find_system_prompt(parent_ctx)
        if system_text:
            full_content.insert(0, {"type": "text", "text": system_text, "role": "system"})

        # --- Call the LLM (with retry) ---
        attempts = exec_ctx.attempts if exec_ctx is not None else []
        for attempt in range(self.max_retries):
            try:
                reply = await self._async_call(full_content, model=use_model, response_format=response_format)
                attempts.append({"attempt": attempt + 1, "reply": reply, "error": None})
                if exec_ctx is not None:
                    exec_ctx.raw_reply = reply
                    exec_ctx.output = reply
                    exec_ctx.status = "success"
                    exec_ctx.end_time = _time.time()
                    _emit_event("node_completed", exec_ctx)
                    parent_ctx.raw_reply = reply
                return reply
            except (TypeError, NotImplementedError):
                raise
            except Exception as e:
                attempts.append({"attempt": attempt + 1, "reply": None, "error": f"{type(e).__name__}: {e}"})
                if attempt == self.max_retries - 1:
                    if exec_ctx is not None:
                        exec_ctx.error = str(e)
                        exec_ctx.status = "error"
                        exec_ctx.end_time = _time.time()
                        _emit_event("node_completed", exec_ctx)
                    error_report = "\n".join(f"Attempt {a['attempt']}: {a['error']}" for a in attempts)
                    raise RuntimeError(
                        f"async_exec() failed after {self.max_retries} attempts in {parent_ctx.name if parent_ctx else 'unknown'}():\n{error_report}"
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


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _find_system_prompt(ctx: Optional["Context"]) -> str:
    """Walk up the Context tree to find the nearest @agentic_function(system=...).

    Returns the first non-empty `system` field encountered, or "" if none.
    Closer ancestors override farther ones (innermost wins).
    """
    node = ctx
    while node is not None:
        if getattr(node, "system", ""):
            return node.system
        node = node.parent
    return ""


def _merge_content(
    context: Optional[str],
    content: list[dict],
    ctx: Optional["Context"],
) -> list[dict]:
    """Merge text content blocks into the context string.

    Text blocks are indented and placed under a "→ Current Task:" marker.
    Non-text blocks (images, audio, files) stay as separate content blocks.
    """
    full_content = []
    if context and ctx is not None:
        # Calculate indent relative to outermost ancestor
        base = ctx._depth()
        node = ctx.parent
        while node and node.name:
            base = node._depth()
            node = node.parent
        exec_indent = "    " * (ctx._depth() - base + 1)

        text_parts = []
        for block in content:
            if block.get("type") == "text":
                indented = "\n".join(
                    exec_indent + line if line.strip() else ""
                    for line in block["text"].splitlines()
                )
                text_parts.append(indented)
            else:
                full_content.append(block)

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
    return full_content
