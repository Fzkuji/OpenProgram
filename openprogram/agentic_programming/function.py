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
from contextvars import ContextVar
from typing import Callable, Optional

import os
from datetime import datetime

import openprogram.agentic_programming.context as _ctx_module
from openprogram.agentic_programming.context import Context, _current_ctx
from openprogram.agentic_programming.events import _emit_event

# Runtime shared across the call chain via ContextVar.
# Entry-point functions auto-create a runtime; child functions inherit it.
_current_runtime: ContextVar = ContextVar('_current_runtime', default=None)

# Parameter names that receive the runtime injection
_RUNTIME_PARAMS = {"runtime", "exec_runtime", "review_runtime"}


class CancelledError(BaseException):
    """Raised by a pre-invocation hook to abort an @agentic_function call.

    Inherits from BaseException (not Exception) so user-written except clauses
    inside @agentic_function bodies don't accidentally swallow cancellation.
    """


# Pre-invocation hooks — called at the top of every @agentic_function wrapper
# BEFORE the user function runs. Any hook can raise (typically CancelledError)
# to abort the call; the exception propagates to the caller unchanged.
_pre_invocation_hooks: list[Callable] = []


def add_pre_invocation_hook(hook: Callable) -> None:
    """Register a hook called at the top of every @agentic_function invocation.

    The hook takes no arguments. It may raise to abort the call (e.g. a
    webui stop button raising CancelledError).
    """
    if hook not in _pre_invocation_hooks:
        _pre_invocation_hooks.append(hook)


def remove_pre_invocation_hook(hook: Callable) -> None:
    """Unregister a previously added pre-invocation hook."""
    try:
        _pre_invocation_hooks.remove(hook)
    except ValueError:
        pass


def _run_pre_invocation_hooks() -> None:
    """Run all registered hooks. Exceptions (including CancelledError) propagate."""
    for hook in list(_pre_invocation_hooks):
        hook()

# Global registry of all @agentic_function-decorated functions.
# Maps function name → agentic_function instance.
# Used by the visualizer to look up source code for any decorated function.
_registry: dict[str, "agentic_function"] = {}


def _inject_runtime(sig, args, kwargs):
    """Auto-inject runtime into function call if needed.

    If the function has a runtime parameter and it's None:
      - If a runtime exists in the call chain (ContextVar), use it.
      - Otherwise, create a new one (this function is the entry point).

    Returns:
        (args, kwargs, runtime_token, owns_runtime)
        - runtime_token: ContextVar token to reset later (or None)
        - owns_runtime: True if we created the runtime (need to close it)
    """
    bound = sig.bind(*args, **kwargs)
    bound.apply_defaults()

    runtime_token = None
    owns_runtime = False

    for param_name in _RUNTIME_PARAMS:
        if param_name in bound.arguments and bound.arguments[param_name] is None:
            # Check call chain first
            rt = _current_runtime.get(None)
            if rt is None:
                # Entry point — create runtime
                from openprogram.providers import create_runtime
                rt = create_runtime()
                runtime_token = _current_runtime.set(rt)
                owns_runtime = True
            bound.arguments[param_name] = rt
            break

    # Also inject for params that exist but weren't provided (positional missing)
    if not owns_runtime and runtime_token is None:
        for param_name in _RUNTIME_PARAMS:
            if param_name in sig.parameters and param_name not in bound.arguments:
                rt = _current_runtime.get(None)
                if rt is None:
                    from openprogram.providers import create_runtime
                    rt = create_runtime()
                    runtime_token = _current_runtime.set(rt)
                    owns_runtime = True
                bound.arguments[param_name] = rt
                break

    # If runtime was provided explicitly and no ContextVar set yet, share it
    if runtime_token is None:
        for param_name in _RUNTIME_PARAMS:
            if param_name in bound.arguments and bound.arguments[param_name] is not None:
                existing = _current_runtime.get(None)
                if existing is None:
                    runtime_token = _current_runtime.set(bound.arguments[param_name])
                break

    return bound.args, bound.kwargs, runtime_token, owns_runtime


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

        input:      UI metadata for function parameters (used by the visualizer
                    to render structured input forms).

                    Dict mapping parameter names to their UI config:
                    {
                        "text": {
                            "description": "The text to analyze",
                            "placeholder": "e.g. I love this product!",
                            "multiline": True,
                        },
                        "style": {
                            "description": "Output style",
                            "placeholder": "academic",
                            "options": ["academic", "casual", "concise"],
                        },
                    }

                    Supported fields per parameter:
                      description  — short label shown next to the parameter name
                      placeholder  — example text shown in the input field
                      multiline    — True for textarea, False for single-line input
                      options      — list of allowed values (renders as dropdown)
                      hidden       — True to hide from the form (e.g. runtime)

                    Parameters not listed inherit defaults from the function
                    signature (type hints, defaults, docstring Args:).
    """

    def __init__(
        self,
        fn: Optional[Callable] = None,
        *,
        render: str = "summary",
        summarize: Optional[dict] = None,
        compress: bool = False,
        input: Optional[dict] = None,
        no_tools: bool = False,
        system: Optional[str] = None,
    ):
        self.render = render
        self.summarize_kwargs = summarize
        self.compress = compress
        self.input_meta = input or {}
        self.no_tools = no_tools
        self.system = system

        self.context = None  # Last executed Context tree (set after top-level call)

        if fn is not None:
            # Used as @agentic_function without parentheses
            self._fn = fn
            self._wrapper = self._make_wrapper(fn)
            functools.update_wrapper(self, fn)
            _registry[fn.__name__] = self
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
            _registry[fn.__name__] = self
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
        system = self.system
        no_tools = self.no_tools

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            # Cancel check / other pre-invocation hooks — may raise to abort.
            _run_pre_invocation_hooks()

            parent = _current_ctx.get(None)

            # Auto-inject runtime if needed
            new_args, new_kwargs, runtime_token, owns_runtime = _inject_runtime(sig, args, kwargs)

            ctx = Context(
                name=fn.__name__,
                prompt=fn.__doc__ or "",
                system=system or "",
                no_tools=no_tools,
                params={},
                parent=parent,
                render=render,
                compress=compress,
                start_time=time.time(),
                _summarize_kwargs=summarize,
            )
            if parent is not None:
                parent.children.append(ctx)
            if parent is None:
                self_ref.context = ctx
            wrapper._last_ctx = ctx

            ctx_token = _current_ctx.set(ctx)
            try:
                # Emit node_created inside the try block so any pre-invocation
                # hook fired by the emit (e.g. pause → stop → CancelledError)
                # is caught by the except branches below and the ctx is marked
                # as cancelled/error rather than orphaned.
                _emit_event("node_created", ctx)
                bound = sig.bind(*new_args, **new_kwargs)
                bound.apply_defaults()
                ctx.params = dict(bound.arguments)

                result = await fn(*new_args, **new_kwargs)
                ctx.output = result
                ctx.status = "success"
                return result
            except CancelledError:
                ctx.error = "Cancelled by user"
                ctx.status = "error"
                raise
            except Exception as e:
                ctx.error = str(e)
                ctx.status = "error"
                raise
            finally:
                ctx.end_time = time.time()
                _emit_event("node_completed", ctx)
                _current_ctx.reset(ctx_token)
                if runtime_token is not None:
                    _current_runtime.reset(runtime_token)
                if owns_runtime:
                    rt = bound.arguments.get("runtime")
                    if rt and hasattr(rt, 'close'):
                        rt.close()
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
        system = self.system
        no_tools = self.no_tools

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # Cancel check / other pre-invocation hooks — may raise to abort.
            _run_pre_invocation_hooks()

            parent = _current_ctx.get(None)

            # Auto-inject runtime if needed
            new_args, new_kwargs, runtime_token, owns_runtime = _inject_runtime(sig, args, kwargs)

            # Create node BEFORE execution so even invalid calls are recorded
            ctx = Context(
                name=fn.__name__,
                prompt=fn.__doc__ or "",
                system=system or "",
                no_tools=no_tools,
                params={},
                parent=parent,
                render=render,
                compress=compress,
                start_time=time.time(),
                _summarize_kwargs=summarize,
            )
            if parent is not None:
                parent.children.append(ctx)
            # Expose context immediately so external observers (e.g. visualizer
            # polling thread) can read the in-progress tree.
            if parent is None:
                self_ref.context = ctx
            wrapper._last_ctx = ctx

            # Set as current context for the duration of the call
            ctx_token = _current_ctx.set(ctx)
            try:
                # Emit node_created inside the try block so any pre-invocation
                # hook fired by the emit (e.g. pause → stop → CancelledError)
                # is caught by the except branches below and the ctx is marked
                # as cancelled/error rather than orphaned.
                _emit_event("node_created", ctx)
                # Bind arguments to record params
                bound = sig.bind(*new_args, **new_kwargs)
                bound.apply_defaults()
                ctx.params = dict(bound.arguments)

                result = fn(*new_args, **new_kwargs)
                ctx.output = result
                ctx.status = "success"
                return result
            except CancelledError:
                ctx.error = "Cancelled by user"
                ctx.status = "error"
                raise
            except Exception as e:
                ctx.error = str(e)
                ctx.status = "error"
                raise
            finally:
                ctx.end_time = time.time()
                wrapper._last_ctx = ctx
                _emit_event("node_completed", ctx)
                _current_ctx.reset(ctx_token)
                # Clean up runtime if we created it
                if runtime_token is not None:
                    _current_runtime.reset(runtime_token)
                if owns_runtime:
                    rt = bound.arguments.get("runtime")
                    if rt and hasattr(rt, 'close'):
                        rt.close()
                # If this was a top-level call (no parent), save and close
                if parent is None:
                    self_ref.context = ctx
                    _auto_save(ctx)

        wrapper._is_agentic = True
        return wrapper


def traced(fn):
    """Lightweight decorator that records function execution in the Context tree.

    Unlike @agentic_function, this does NOT involve any LLM logic — it simply
    creates a Context node so the function appears in the Execution Tree.

    Usage:
        @traced
        def search_papers(query):
            ...
    """
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        parent = _current_ctx.get(None)
        if parent is None:
            # No active context tree — run without tracing
            return fn(*args, **kwargs)

        ctx = Context(
            name=fn.__name__,
            prompt=fn.__doc__ or "",
            params={},
            parent=parent,
            start_time=time.time(),
        )
        parent.children.append(ctx)
        token = _current_ctx.set(ctx)
        _emit_event("node_created", ctx)
        try:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            ctx.params = {k: v for k, v in bound.arguments.items()
                          if k not in ("self", "cls", "runtime", "callback")}
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
            _emit_event("node_completed", ctx)
            _current_ctx.reset(token)

    wrapper._is_traced = True
    return wrapper


def _is_agentic_obj(obj) -> bool:
    """Check if an object is an @agentic_function (class instance or wrapper)."""
    if isinstance(obj, agentic_function):
        return True
    return getattr(obj, '_is_agentic', False)


def _calls_agentic(func, mod) -> bool:
    """Check if a function calls any @agentic_function.

    Inspects the function's bytecode references (co_names) and checks
    whether any referenced name in the module is an @agentic_function.
    This identifies orchestrator functions that should be traced.
    """
    # Unwrap decorated functions to get the original code
    original = getattr(func, '__wrapped__', func)
    try:
        code_names = set(original.__code__.co_names)
    except AttributeError:
        return False
    for ref_name in code_names:
        ref_obj = getattr(mod, ref_name, None)
        if ref_obj is not None and _is_agentic_obj(ref_obj):
            return True
    return False


def auto_trace_module(mod, exclude=None, trace_pkg=None):
    """Auto-apply @traced to orchestrator functions in a module.

    Only traces functions that call @agentic_function (orchestrators).
    Leaf functions (pure utilities like compute_iou) are skipped.

    Skips functions that are already @agentic_function or @traced,
    private functions (starting with _), and third-party imports.

    Args:
        mod: The module object to patch.
        exclude: Optional set of function names to skip.
        trace_pkg: Package directory path. Functions from files within this
                   directory are considered even if imported. If None, uses
                   the directory of mod.__file__.
    """
    exclude = exclude or set()
    mod_file = getattr(mod, '__file__', None)
    if not mod_file:
        return
    if trace_pkg is None:
        trace_pkg = os.path.dirname(os.path.abspath(mod_file))

    for name in list(dir(mod)):
        if name.startswith('_') or name in exclude:
            continue
        obj = getattr(mod, name)
        if not callable(obj) or not inspect.isfunction(obj):
            continue
        # Skip already decorated
        if getattr(obj, '_is_agentic', False) or getattr(obj, '_is_traced', False):
            continue
        # Only trace functions defined within the package
        try:
            fn_file = os.path.abspath(inspect.getfile(obj))
        except (TypeError, OSError):
            continue
        if not fn_file.startswith(trace_pkg):
            continue
        # Only trace orchestrators (functions that call @agentic_function)
        if _calls_agentic(obj, mod):
            setattr(mod, name, traced(obj))


def auto_trace_package(pkg_dir, pkg_name=None):
    """Recursively auto-trace all .py files in a package directory.

    Walks the directory tree, imports each module, and applies @traced
    to all user-defined functions. This ensures that lazy imports
    within the package get traced versions.

    Args:
        pkg_dir: Absolute path to the package root directory.
        pkg_name: Dotted package name prefix (e.g. "research_harness").
                  If None, uses the directory basename.
    """
    import importlib.util as _imputil
    import sys as _sys

    pkg_dir = os.path.abspath(pkg_dir)
    if pkg_name is None:
        pkg_name = os.path.basename(pkg_dir)

    for root, dirs, files in os.walk(pkg_dir):
        dirs[:] = [d for d in dirs if not d.startswith(("_", ".", "test"))]
        for f in sorted(files):
            if not f.endswith(".py") or f.startswith("_"):
                continue
            filepath = os.path.join(root, f)
            # Build module name relative to pkg_dir
            rel = os.path.relpath(filepath, os.path.dirname(pkg_dir))
            mod_name = rel.replace(os.sep, ".")[:-3]  # strip .py
            if mod_name in _sys.modules:
                mod = _sys.modules[mod_name]
            else:
                try:
                    spec = _imputil.spec_from_file_location(mod_name, filepath)
                    if spec is None:
                        continue
                    mod = _imputil.module_from_spec(spec)
                    _sys.modules[mod_name] = mod
                    spec.loader.exec_module(mod)
                except Exception:
                    continue
            auto_trace_module(mod, trace_pkg=pkg_dir)


def _auto_save(ctx: Context):
    """Auto-save the completed Context tree to the logs directory."""
    try:
        logs_dir = os.path.join(os.path.dirname(__file__), "logs")
        os.makedirs(logs_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{ctx.name}_{timestamp}.jsonl"
        path = os.path.join(logs_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            for record in ctx._to_event_records():
                f.write(__import__("json").dumps(record, ensure_ascii=False, default=str) + "\n")
        ctx._persist_path = path
    except Exception:
        pass  # Never fail the user's function because of logging
