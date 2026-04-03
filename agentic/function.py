"""
@agentic_function — decorator that auto-tracks function execution in the Context tree.

Two settings:
    render:     How this function's results are displayed to others
    summarize:  What context this function sees when calling the LLM
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
    render: str = "summary",
    summarize: Optional[dict] = None,
):
    """
    Decorator: marks a function as an Agentic Function.

    Every decorated function is unconditionally recorded into the Context tree.

    Args:
        render:    How others see my results via summarize().
                   trace / detail / summary (default) / result / silent
                   This is a default — callers can override with summarize(level=...).

        summarize: Dict of parameters passed to ctx.summarize() when runtime.exec()
                   auto-generates context for this function's LLM calls.
                   Example: {"depth": 1, "siblings": 3}
                   If None, uses default summarize() (all ancestors + all siblings).
    """
    def decorator(fn: Callable) -> Callable:
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            params = dict(bound.arguments)

            # Attach to parent, or create root if none exists
            parent = _current_ctx.get(None)
            if parent is None:
                parent = Context(
                    name="root",
                    start_time=time.time(),
                    status="running",
                )
                _current_ctx.set(parent)

            ctx = Context(
                name=fn.__name__,
                prompt=fn.__doc__ or "",
                params=params,
                parent=parent,
                render=render,
                start_time=time.time(),
                _summarize_kwargs=summarize,
            )
            parent.children.append(ctx)

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
        return wrapper

    if fn is not None:
        return decorator(fn)
    return decorator
