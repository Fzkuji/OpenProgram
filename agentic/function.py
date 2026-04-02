"""
@agentic_function — decorator that auto-tracks function execution in the Context tree.

This is the ONLY thing users need to add to their code. Everything else is automatic.

Three dimensions of control:
    expose:   How OTHERS see MY results     → "summary", "detail", "result", etc.
    context:  How I ATTACH to the tree      → "auto", "inherit", "new", "none"
    depth/siblings/...: What I SEE from the tree → controls context injection

Usage:
    @agentic_function
    def navigate(target): ...

    @agentic_function(expose="detail", context="inherit", depth=1, siblings=3)
    def observe(task): ...

    @agentic_function(context="inherit", decay=True)
    def observe_in_loop(task): ...
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
    # --- How others see me ---
    expose: str = "summary",
    # --- How I attach to the tree ---
    context: str = "auto",
    # --- What I see from the tree (context injection) ---
    depth: int = -1,
    siblings: int = -1,
    level: str = "summary",
    decay: bool = False,
    decay_thresholds: Optional[list] = None,
    decay_fallback_window: int = 1,
    decay_fallback_level: str = "result",
    progressive_detail: Optional[list] = None,
    cache_stable: bool = True,
    include: Optional[list] = None,
    exclude: Optional[list] = None,
    branch: Optional[list] = None,
    max_tokens: Optional[int] = None,
):
    """
    Decorator: marks a function as an Agentic Function.

    Args:
        expose:  How others see my results in summarize().
                 trace / detail / summary (default) / result / silent

        context: How I attach to the Context tree.
                 "auto" / "inherit" / "new" / "none"

        depth:    How many ancestor levels I see. -1=all, 0=none, 1=parent only.
        siblings: How many previous siblings I see. -1=all, 0=none, N=last N.
        level:    Default render level for siblings I see. Same values as expose.
        decay:    Auto-reduce visible siblings as call count grows.
        decay_thresholds: List of (max_n_siblings, window, level). First match wins.
        decay_fallback_window: Window when exceeding all thresholds.
        decay_fallback_level: Level when exceeding all thresholds.
        progressive_detail: Vary level by recency. List of (recency, level).
        cache_stable: Freeze sibling renderings for prompt cache stability.
        include:  Path whitelist (supports * wildcard).
        exclude:  Path blacklist (supports * wildcard).
        branch:   Show subtree under these node names.
        max_tokens: Token budget for context injection.
    """
    # Build a ContextPolicy from the parameters
    # (only if any non-default injection setting was specified)
    has_custom_injection = (
        depth != -1 or siblings != -1 or level != "summary" or
        decay or progressive_detail is not None or
        not cache_stable or include is not None or exclude is not None or
        branch is not None or max_tokens is not None
    )

    if has_custom_injection:
        policy = ContextPolicy(
            depth=depth,
            siblings=siblings,
            level=level,
            decay=decay,
            decay_thresholds=decay_thresholds or [
                (5, -1, "detail"),
                (15, 3, "summary"),
            ],
            decay_fallback_window=decay_fallback_window,
            decay_fallback_level=decay_fallback_level,
            progressive_detail=progressive_detail,
            cache_stable=cache_stable,
            include=include,
            exclude=exclude,
            branch=branch,
            max_tokens=max_tokens,
        )
    else:
        policy = None

    def decorator(fn: Callable) -> Callable:
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # context="none": no tracking
            if context == "none":
                return fn(*args, **kwargs)

            # Capture call params
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            params = dict(bound.arguments)

            # Determine parent based on context mode
            parent = _current_ctx.get(None)

            if context == "new":
                parent = None
            elif context == "inherit":
                if parent is None:
                    raise RuntimeError(
                        f"{fn.__name__}() requires a parent context "
                        f"(context='inherit'), but none exists. "
                        f"Call it from within another @agentic_function."
                    )
            elif context == "auto":
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
                _policy=policy,
            )
            if parent is not None:
                parent.children.append(ctx)

            # Execute
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

    if fn is not None:
        return decorator(fn)
    return decorator
