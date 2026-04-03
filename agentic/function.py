"""
agentic_function — decorator class that records function execution into the Context tree.

Usage is identical to a decorator function:

    @agentic_function
    def observe(task): ...

    @agentic_function(render="detail", summarize={"depth": 1}, compress=True)
    def navigate(target): ...

Internally it's a class (like torch.no_grad), but users interact with it
as a decorator. The class form allows clean documentation and introspection.
"""

from __future__ import annotations

import functools
import inspect
import time
from typing import Callable, Optional

import os
from datetime import datetime

import agentic.context as _ctx_module
from agentic.context import Context, _current_ctx


class agentic_function:
    """
    Decorator that records function execution into the Context tree.

    Every decorated function is unconditionally recorded. On entry, a new
    Context node is created. On exit, the node is updated with the return
    value (or error) and timing.

    Args:
        render:     How others see my results via summarize().

                    "summary" — name, docstring, params, output, status, duration (DEFAULT)
                    "detail"  — summary + LLM raw_reply
                    "result"  — name + return value only
                    "silent"  — not shown

                    This is a default. Callers can override per-query:
                    ctx.summarize(level="detail") overrides all nodes' render.

        summarize:  What context I see when runtime.exec() auto-injects context.

                    Dict of keyword arguments passed to ctx.summarize().
                    Example: {"depth": 1, "siblings": 3}

                    If None (default), runtime.exec() calls ctx.summarize()
                    with no arguments → all ancestors + all siblings.

                    Common patterns:
                      {"depth": 0, "siblings": 0}    — isolated, see nothing
                      {"depth": 1, "siblings": 1}    — parent + last sibling
                      {"siblings": 3}                 — all ancestors + last 3

        compress:   After this function completes, hide children from summarize().

                    When True, other functions calling summarize() see only this
                    node's own rendered result — the children (sub-calls) are NOT
                    expanded, even if branch= is used.

                    The children are still fully recorded in the tree. tree() and
                    save() always show everything. compress only affects summarize().

                    Default: False.
    """

    def __init__(
        self,
        fn: Optional[Callable] = None,
        *,
        render: str = "summary",
        summarize: Optional[dict] = None,
        compress: bool = False,
    ):
        self.render = render
        self.summarize_kwargs = summarize
        self.compress = compress

        self.context = None  # Last executed Context tree (set after top-level call)

        if fn is not None:
            # Used as @agentic_function without parentheses
            self._fn = fn
            self._wrapper = self._make_wrapper(fn)
            functools.update_wrapper(self, fn)
        else:
            # Used as @agentic_function(...) with arguments
            self._fn = None
            self._wrapper = None

    def __call__(self, *args, **kwargs):
        if self._fn is not None:
            # @agentic_function (no parens) — self is the decorator,
            # __call__ is invoked with the actual function arguments
            return self._wrapper(*args, **kwargs)
        else:
            # @agentic_function(...) — first __call__ receives the function
            fn = args[0]
            self._fn = fn
            self._wrapper = self._make_wrapper(fn)
            functools.update_wrapper(self, fn)
            return self

    def __get__(self, obj, objtype=None):
        """Support instance methods."""
        if obj is None:
            return self
        return functools.partial(self._wrapper, obj)

    def _make_wrapper(self, fn: Callable) -> Callable:
        sig = inspect.signature(fn)

        if inspect.iscoroutinefunction(fn):
            return self._make_async_wrapper(fn, sig)
        return self._make_sync_wrapper(fn, sig)

    def _make_async_wrapper(self, fn: Callable, sig: inspect.Signature) -> Callable:
        self_ref = self
        render = self.render
        compress = self.compress
        summarize = self.summarize_kwargs

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            parent = _current_ctx.get(None)

            ctx = Context(
                name=fn.__name__,
                prompt=fn.__doc__ or "",
                params={},
                parent=parent,
                render=render,
                compress=compress,
                start_time=time.time(),
                _summarize_kwargs=summarize,
            )
            if parent is not None:
                parent.children.append(ctx)

            token = _current_ctx.set(ctx)
            try:
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                ctx.params = dict(bound.arguments)

                result = await fn(*args, **kwargs)
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
                if parent is None:
                    self_ref.context = ctx
                    _auto_save(ctx)

        wrapper._is_agentic = True
        return wrapper

    def _make_sync_wrapper(self, fn: Callable, sig: inspect.Signature) -> Callable:
        self_ref = self
        render = self.render
        compress = self.compress
        summarize = self.summarize_kwargs

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            parent = _current_ctx.get(None)

            # Create node BEFORE binding so even invalid calls are recorded
            ctx = Context(
                name=fn.__name__,
                prompt=fn.__doc__ or "",
                params={},
                parent=parent,
                render=render,
                compress=compress,
                start_time=time.time(),
                _summarize_kwargs=summarize,
            )
            if parent is not None:
                parent.children.append(ctx)

            # Set as current context for the duration of the call
            token = _current_ctx.set(ctx)
            try:
                # Bind arguments (inside try so binding errors are recorded)
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                ctx.params = dict(bound.arguments)

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
                # If this was a top-level call (no parent), save and close
                if parent is None:
                    self_ref.context = ctx
                    _auto_save(ctx)

        wrapper._is_agentic = True
        return wrapper


def _auto_save(ctx: Context):
    """Auto-save the completed Context tree to the logs directory."""
    try:
        logs_dir = os.path.join(os.path.dirname(__file__), "logs")
        os.makedirs(logs_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{ctx.name}_{timestamp}.jsonl"
        ctx.save(os.path.join(logs_dir, filename))
    except Exception:
        pass  # Never fail the user's function because of logging
