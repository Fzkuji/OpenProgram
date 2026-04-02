"""
@agentic_function — decorator that auto-tracks function execution in the Context tree.

Usage:
    @agentic_function
    def observe(task):
        '''Look at the screen...'''
        ...

    @agentic_function(expose="detail")
    def observe(task):
        '''Look at the screen...'''
        ...
"""

from __future__ import annotations

import functools
import inspect
import time
from typing import Callable, Optional

from agentic.context import Context, _current_ctx


def agentic_function(
    fn: Optional[Callable] = None,
    *,
    expose: str = "summary",
    context: str = "auto",
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
        expose:  Visibility level for summarize() (trace/detail/summary/result/silent)
        context: How to attach to the Context tree:
                 - "auto":    attach to parent if exists, else create root (default)
                 - "new":     always create an independent tree
                 - "inherit": must have parent, raises if none
                 - "none":    skip context tracking entirely
    
    Usage:
        @agentic_function
        def observe(task): ...
        
        @agentic_function(expose="detail", context="inherit")
        def observe(task): ...
    """
    def decorator(fn: Callable) -> Callable:
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # context="none" → skip tracking entirely
            if context == "none":
                return fn(*args, **kwargs)

            # Capture call params
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            params = dict(bound.arguments)

            # Determine parent based on context mode
            parent = _current_ctx.get(None)

            if context == "new":
                # Always create independent tree
                parent = None
            elif context == "inherit":
                # Must have parent
                if parent is None:
                    raise RuntimeError(
                        f"{fn.__name__}() requires a parent context "
                        f"(context='inherit'), but none exists. "
                        f"Call it from within another @agentic_function."
                    )
            elif context == "auto":
                # If no parent, auto-create root
                if parent is None:
                    parent = Context(
                        name="root",
                        start_time=time.time(),
                        status="running",
                    )
                    _current_ctx.set(parent)

            # Create Context node
            ctx = Context(
                name=fn.__name__,
                prompt=fn.__doc__ or "",
                params=params,
                parent=parent,
                expose=expose,
                start_time=time.time(),
            )
            if parent is not None:
                parent.children.append(ctx)

            # Set as current
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
                _current_ctx.reset(token)

        wrapper._is_agentic = True
        wrapper._expose = expose
        wrapper._context_mode = context
        return wrapper

    # Support both @agentic_function and @agentic_function(expose="detail")
    if fn is not None:
        return decorator(fn)
    return decorator
