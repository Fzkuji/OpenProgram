"""
@agentic_function — decorator that auto-tracks function execution in the Context tree.

This is the ONLY thing users need to add to their code. Everything else is automatic.
The decorator intercepts function calls, creates Context nodes, and manages the tree.

Usage:
    @agentic_function
    def observe(task):
        '''Look at the screen...'''
        img = take_screenshot()
        return runtime.exec(prompt=observe.__doc__, input={"task": task}, images=[img])

    @agentic_function(expose="detail", context="inherit")
    def act(target, location):
        '''Click the target.'''
        click(location)
        return {"clicked": True}

Design decisions (lessons learned):

    1. We tried THREE approaches for how functions access their Context:
       - v1: Pass ctx as argument → users can pass wrong ctx, error-prone
       - v2: get_context() inside function → extra boilerplate, ugly
       - v3: Users don't touch ctx at all → WINNER. Zero framework code in user functions.
       The key insight: if runtime.exec() auto-records to Context, users never need ctx.

    2. The `context` parameter controls tree attachment:
       - "auto": creates root if needed (most common, zero setup)
       - "new": independent tree (for background tasks)
       - "inherit": must be called from another agentic function (enforced)
       - "none": skip tracking entirely (pure Python, no overhead)
       We added "auto" root creation after Codex review pointed out that
       without init_root(), the Context tree was lost after execution.

    3. expose is a RENDERING HINT passed to Context.summarize().
       It is NOT a security boundary — summarize(level=...) can override it.
       We debated calling it "visibility" or "share_level" but kept "expose"
       for brevity. The Codex reviewer flagged this naming as ambiguous.

    4. async def is NOT supported yet. Decorating an async function will
       silently produce wrong results (coroutine stored as output).
       TODO: Either support async properly or reject at decoration time.
"""

from __future__ import annotations

import functools
import inspect
import time
from typing import Callable, Optional

from agentic.context import Context, ContextPolicy, _current_ctx


def agentic_function(
    fn: Optional[Callable] = None,
    *,
    expose: str = "summary",
    context: str = "auto",
    context_policy: Optional[ContextPolicy] = None,
):
    """
    Decorator: marks a function as an Agentic Function.
    
    Automatically tracks:
    - name (from __name__)
    - prompt (from __doc__)
    - params (from call arguments)
    - output (from return value)
    - error (from exceptions)
    - status, timing, children, parent
    
    Args:
        expose:  Visibility level for summarize() rendering.
                 How OTHER functions see MY results.
                 trace / detail / summary (default) / result / silent
        
        context: How to attach to the Context tree:
                 - "auto":    attach to parent if exists, else create root (default)
                 - "new":     always create an independent tree (background tasks)
                 - "inherit": must have parent, raises RuntimeError if none
                 - "none":    skip context tracking entirely (pure Python)
        
        context_policy: How I see OTHERS' results when runtime.exec() is called.
                 Controls what context gets auto-injected into my LLM calls.
                 Use presets: ORCHESTRATOR, PLANNER, WORKER, LEAF, FOCUSED.
                 Or create a custom ContextPolicy.
                 If None, uses default summarize() (all ancestors + all siblings).
    
    Usage:
        @agentic_function
        def observe(task): ...
        
        @agentic_function(expose="detail", context="inherit", context_policy=WORKER)
        def observe(task): ...
    """
    def decorator(fn: Callable) -> Callable:
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # --- context="none": no tracking at all ---
            if context == "none":
                return fn(*args, **kwargs)

            # --- Capture call params ---
            # Note: if bind() raises (wrong arguments), it happens BEFORE
            # Context creation, so the error won't be tracked. This is
            # acceptable — it's a Python-level error, not an agentic one.
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            params = dict(bound.arguments)

            # --- Determine parent based on context mode ---
            parent = _current_ctx.get(None)

            if context == "new":
                # Independent tree — ignore any existing parent
                parent = None
            elif context == "inherit":
                # Must have a parent — this function should only be called
                # from within another @agentic_function
                if parent is None:
                    raise RuntimeError(
                        f"{fn.__name__}() requires a parent context "
                        f"(context='inherit'), but none exists. "
                        f"Call it from within another @agentic_function."
                    )
            elif context == "auto":
                # Default mode: if no parent exists, auto-create a root.
                # This was added after we realized that without init_root(),
                # the Context tree was silently lost after execution.
                if parent is None:
                    parent = Context(
                        name="root",
                        start_time=time.time(),
                        status="running",
                    )
                    _current_ctx.set(parent)

            # --- Create this function's Context node ---
            ctx = Context(
                name=fn.__name__,
                prompt=fn.__doc__ or "",
                params=params,
                parent=parent,
                expose=expose,
                start_time=time.time(),
                _policy=context_policy,  # Stored for runtime.exec() to use
            )
            if parent is not None:
                parent.children.append(ctx)

            # --- Execute with this node as the current context ---
            # _current_ctx.set() returns a token for reset.
            # Any @agentic_function called inside fn() will see ctx as parent.
            token = _current_ctx.set(ctx)
            try:
                result = fn(*args, **kwargs)
                ctx.output = result
                ctx.status = "success"
                return result
            except Exception as e:
                ctx.error = str(e)
                ctx.status = "error"
                raise
            finally:
                ctx.end_time = time.time()
                # Restore the previous context (parent becomes current again)
                _current_ctx.reset(token)

        # Metadata on the wrapper for introspection
        wrapper._is_agentic = True
        wrapper._expose = expose
        wrapper._context_mode = context
        wrapper._context_policy = context_policy
        return wrapper

    # Support both @agentic_function and @agentic_function(expose="detail")
    if fn is not None:
        return decorator(fn)
    return decorator
