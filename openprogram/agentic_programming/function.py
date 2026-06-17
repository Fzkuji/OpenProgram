"""
agentic_function — decorator class that records function execution into the DAG.

Usage is identical to a decorator function:

    @agentic_function
    def observe(task): ...

    @agentic_function(expose="full", render_range={"callers": 1})
    def navigate(target): ...

Internally it's a class (like torch.no_grad), but users interact with it
as a decorator. The class form allows clean documentation and introspection.
"""

from __future__ import annotations

import functools
import inspect
import os
import time
from contextvars import ContextVar
from typing import Callable, Optional

# Runtime shared across the call chain via ContextVar.
# Entry-point functions auto-create a runtime; child functions inherit it.
_current_runtime: ContextVar = ContextVar('_current_runtime', default=None)

# DAG call_id of the @agentic_function currently being executed in this
# task. The decorator sets it at entry; Python's ContextVar set/reset
# token gives us scope-bound semantics for free, so nested invocations
# automatically restore the outer caller's id on exit. Downstream code
# (``Runtime.exec``, ``ask_user``) reads this to stamp the
# ``called_by`` field on whatever DAG node it appends.
_call_id: ContextVar[Optional[str]] = ContextVar(
    '_call_id', default=None,
)

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


def _append_function_call_entry(
    *,
    pending_id: str,
    function_name: str,
    arguments: dict,
    expose: str,
    render_range,
    started_at,
    docstring: str = "",
) -> None:
    """Append a placeholder code Call at @agentic_function entry.

    The node has ``output=None`` (function hasn't returned yet) and
    ``metadata.status='running'``. The matching
    :func:`_update_function_call_exit` fills these in at exit.

    ``render_range`` is stamped into metadata so ``compute_reads``
    (which reads frame settings off the in-DAG code Call) can apply
    callers / subcalls limits without needing a separate in-memory frame.

    No-op when:
      - no ``_store`` is installed (standalone scripts / tests)
      - ``expose='hidden'`` (caller wants no trace in the DAG)
    """
    if expose == "hidden":
        return

    from openprogram.store import _store
    store = _store.get()
    if store is None:
        return

    from openprogram.context.nodes import Call, ROLE_CODE

    meta: dict = {
        "expose": expose,
        "status": "running",
    }
    if render_range:
        meta["render_range"] = dict(render_range)
    # The function's docstring travels on the node so it renders into
    # the context of any LLM call that reads this code Call — restoring
    # the tree-Context behaviour where a function's documentation was
    # visible to the model running inside it.
    if docstring:
        meta["doc"] = docstring
    node = Call(
        id=pending_id,
        created_at=started_at or time.time(),
        role=ROLE_CODE,
        name=function_name,
        input=_sanitize_function_args(arguments or {}),
        output=None,
        # ``called_by`` is the logical caller — the @agentic_function
        # whose body is the one invoking us. ``_call_id`` is set by
        # the outer wrapper before we run; reading it now gives us
        # the right ancestor. Empty string when this is a top-level
        # call (no enclosing @agentic_function on the call stack).
        called_by=_call_id.get() or "",
        metadata=meta,
    )
    try:
        store.append(node)
    except Exception:
        # DAG persistence failure must never break the user's function call.
        pass


def _update_function_call_exit(
    *,
    pending_id: str,
    output,
    error,
    status: str,
    expose: str,
    started_at,
    ended_at,
) -> None:
    """Fill in output + status on the placeholder Call written at entry.

    Mirror of :func:`_append_function_call_entry` — same no-op rules.
    """
    if expose == "hidden":
        return

    from openprogram.store import _store
    store = _store.get()
    if store is None:
        return

    duration = None
    if started_at is not None and ended_at is not None:
        duration = float(ended_at) - float(started_at)

    if status == "error":
        result_payload = {"error": error or "unknown"}
    else:
        result_payload = output

    try:
        store.update(
            pending_id,
            output=result_payload,
            metadata={
                "status": status,
                "duration_seconds": duration,
            },
        )
    except Exception:
        pass


def _sanitize_function_args(params: dict) -> dict:
    """Trim non-JSON-friendly param values so they fit a data_json blob.

    - Runtime injections become a type tag (we don't want to serialise
      a whole Runtime object into SQLite on every call).
    - Anything that JSON-doesn't-like is repr'd and truncated to 500 chars.
    """
    out: dict = {}
    for k, v in params.items():
        if k in _RUNTIME_PARAMS:
            out[k] = f"<{type(v).__name__}>"
            continue
        try:
            import json as _json
            _json.dumps(v, default=str)
            out[k] = v
        except (TypeError, ValueError):
            out[k] = repr(v)[:500]
    return out




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
    # bind_partial, NOT bind: a runtime parameter with no default (e.g.
    # `def f(pdf_path, runtime, …)`) is REQUIRED, so a full `sig.bind`
    # raises "missing a required argument: 'runtime'" here — before we
    # ever get a chance to inject it. bind_partial tolerates the gap so
    # the injection below (the positional-missing branch) can fill it;
    # the caller's own `sig.bind` (in the wrapper, after injection) still
    # enforces that every other required argument was supplied.
    bound = sig.bind_partial(*args, **kwargs)
    bound.apply_defaults()

    runtime_token = None
    owns_runtime = False

    # Which runtime params this function declares, and which still need a
    # value (either bound to None via default, or missing entirely). A
    # function can declare MORE THAN ONE (e.g. research_agent's `runtime`
    # + `review_runtime`) — every one of them must be filled, not just the
    # first. The old code `break`ed after one, so with _RUNTIME_PARAMS
    # being an unordered set, which param got filled was nondeterministic
    # → research_agent intermittently saw `runtime=None` and raised.
    declared = [p for p in sig.parameters if p in _RUNTIME_PARAMS]
    needs = [
        p for p in declared
        if (p in bound.arguments and bound.arguments[p] is None)
        or (p not in bound.arguments)
    ]

    # Lazily resolve ONE runtime (from the call chain, else create one)
    # and share it across all the params that need it.
    def _resolve_rt():
        nonlocal runtime_token, owns_runtime
        rt = _current_runtime.get(None)
        if rt is None:
            from openprogram.providers.registry import create_runtime
            try:
                rt = create_runtime()
            except RuntimeError:
                # No LLM provider configured. In production we re-raise the
                # helpful "set up a provider" guidance. Under pytest with no
                # credentials (CI), fall back to a placeholder runtime whose
                # .exec raises only IF the body actually calls the model —
                # so the many tests whose bodies never touch the LLM (cache /
                # timeout wrappers, dispatcher plumbing) stop crashing on a
                # provider lookup they don't need. See tests/conftest.py.
                import os as _os
                if not _os.environ.get("PYTEST_CURRENT_TEST"):
                    raise
                from openprogram.agentic_programming.runtime import Runtime

                def _no_provider_call(content, model="test", response_format=None):
                    raise RuntimeError(
                        "No LLM provider configured (test placeholder runtime). "
                        "This test body called the model without providing a "
                        "runtime; pass one explicitly or mock the LLM."
                    )

                rt = Runtime(call=_no_provider_call, model="test")
            runtime_token = _current_runtime.set(rt)
            owns_runtime = True
        return rt

    if needs:
        rt = _resolve_rt()
        for p in needs:
            bound.arguments[p] = rt

    # A runtime was passed in explicitly (not None) and nothing is in the
    # call chain yet → publish it so nested calls inherit the same one.
    if runtime_token is None:
        for p in declared:
            if bound.arguments.get(p) is not None:
                if _current_runtime.get(None) is None:
                    runtime_token = _current_runtime.set(bound.arguments[p])
                break

    return bound.args, bound.kwargs, runtime_token, owns_runtime


def _apply_system(system, bound_args):
    """Apply a function's decorator ``system=`` onto its injected
    runtime(s) for the duration of the call.

    ``runtime.exec`` reads the system prompt off ``runtime.system``, so
    the decorator's ``system=`` only reaches the model if it is stamped
    there. Returns a restore list consumed by :func:`_restore_system`
    so a caller's own ``system`` is not clobbered by a nested call.
    """
    if not system:
        return []
    saved = []
    seen = set()
    for pname in _RUNTIME_PARAMS:
        rt = bound_args.get(pname)
        if rt is None or id(rt) in seen:
            continue
        seen.add(id(rt))
        had = hasattr(rt, "system")
        prev = getattr(rt, "system", None)
        try:
            rt.system = system
        except Exception:
            continue
        saved.append((rt, had, prev))
    return saved


def _restore_system(saved):
    """Undo :func:`_apply_system`."""
    for rt, had, prev in saved:
        try:
            if had:
                rt.system = prev
            else:
                delattr(rt, "system")
        except Exception:
            pass


class agentic_function:
    """
    Class decorator for functions whose body spawns an inner agent loop.

    Two roles per decorated function:

      1. **Python-direct-invoke**: ``research("topic")`` triggers
         ``__call__`` → runs the wrapper → executes the function body
         (which usually calls ``runtime.exec(...)`` to drive an inner
         LLM round). Used when another @agentic_function composes this
         one as a Python building block — no LLM round-trip on the
         outer side, just nested execution.

      2. **LLM tool dispatch**: every instance bridges itself into the
         shared ``openprogram.functions._runtime._registry`` via
         ``_register_as_tool`` (delegating to the same
         ``_build_and_register_tool`` helper ``@function`` uses). From
         the dispatcher's perspective the result is an ``AgentTool``
         indistinguishable from one produced by ``@function``, so all
         6 selection layers (``available_if`` / toolset / mode preset
         / ``check_fn`` / deny rules / ``defer``) apply uniformly.

    Both roles share one underlying ``self._wrapper`` that carries the
    DAG-recording semantics: on entry a placeholder code Call (status
    ``running``, ``output=None``) is appended to the GraphStore; on
    exit the same node is updated with the return value (or error)
    and timing. Set ``expose="hidden"`` to skip DAG recording.

    Args:
        expose:     What outside observers see of me after I complete. [DEFAULT: "io"]

                    "io"     — only name + return value (internals hidden)
                    "llm"    — only my LLM exchanges (my own name + return
                               value and my nested code sub-calls hidden)
                    "full"   — docstring + params + output + LLM reply + internals
                    "hidden" — no DAG node at all

                    ``expose`` is stamped into the code Call's metadata;
                    ``compute_reads`` uses it to decide whether a later
                    LLM call can see this function's internal nodes.

        render_range: What slice of the DAG I bring into my own LLM calls.

                    Dict stamped into the code Call's metadata; the
                    runtime's ``compute_reads`` reads it to bound the
                    history a nested ``runtime.exec`` sees.
                    Shape: {"callers": N, "subcalls": M}.

                    Effective default when omitted (``None``):
                      callers  = None  — uncapped pre-frame (the full
                                         conversation history that
                                         existed when this function
                                         started flows in)
                      subcalls = -1    — uncapped in-frame (the frame
                                         naturally sees its own
                                         progress: earlier runtime.exec
                                         results and returned sub-
                                         function io). Trimming a
                                         child @agentic_function's
                                         internals is done by the
                                         child's ``expose`` setting,
                                         not by subcalls counting.

                    Common patterns:
                      {"callers": 0}                  — isolated from
                                                        prior conversation
                      {"subcalls": 0}                 — wall off in-frame
                                                        (rarely needed)
                      {"subcalls": 3}                 — cap in-frame at
                                                        3 most recent
                                                        (loop budget)

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
        # —— agentic-specific ——
        expose: str = "io",
        render_range: Optional[dict] = None,
        input: Optional[dict] = None,
        system: Optional[str] = None,
        # WebUI working-directory picker mode. The decorator itself only
        # stores it (the value is read out of the SOURCE TEXT by the
        # webui's AST extractor, openprogram/webui/_functions.py) —
        # accepting it here is what makes writing the kwarg legal: an
        # unknown kwarg would TypeError at import and kill the module.
        workdir_mode: Optional[str] = None,
        # —— shared with @function ——
        # The function-calling refactor unified these names with the
        # @function decorator so an @agentic_function and an @function
        # produce equivalent ``AgentTool`` entries in the same
        # ``openprogram.functions._runtime._registry``. The agentic
        # decorator adds DAG recording + inner agent loop spawning on
        # top of the shared registration machinery.
        as_tool: bool = True,
        name: Optional[str] = None,
        description: Optional[str] = None,
        parameters: Optional[dict] = None,
        label: Optional[str] = None,
        toolset: tuple = (),
        unsafe_in: tuple = (),
        check_fn: Optional[Callable] = None,
        requires_env: tuple = (),
        can_use: Optional[Callable] = None,
        max_result_chars: Optional[int] = None,
        persist_full: bool = False,
        head_ratio: Optional[float] = None,
        requires_approval=None,
        cache: bool = False,
        cache_ttl: float = 300.0,
        timeout: Optional[float] = None,
        # Layer 1 + Layer 6 (same shapes as @function)
        available_if: Optional[Callable[[], bool]] = None,
        defer: bool = False,
        register_globally: bool = True,
    ):
        if expose not in ("io", "llm", "full", "hidden"):
            raise ValueError(
                f"expose must be 'io', 'llm', 'full', or 'hidden', "
                f"got {expose!r}"
            )
        if workdir_mode not in (None, "optional", "hidden", "required"):
            raise ValueError(
                f"workdir_mode must be 'optional', 'hidden', or 'required', "
                f"got {workdir_mode!r}"
            )
        self.expose = expose
        self.render_range = render_range
        self.input_meta = input or {}
        self.workdir_mode = workdir_mode
        self.system = system
        self.as_tool = as_tool
        self.tool_name = name
        self.tool_description = description
        self.tool_parameters = parameters
        self.tool_label = label
        self.toolset = tuple(toolset)
        self.unsafe_in = tuple(unsafe_in)
        self.check_fn = check_fn
        self.requires_env = tuple(requires_env)
        self.max_result_chars = max_result_chars
        self.persist_full = persist_full
        self.head_ratio = head_ratio
        self.requires_approval = requires_approval
        self.cache = cache
        self.cache_ttl = cache_ttl
        self.timeout = timeout
        self.can_use = can_use
        self.available_if = available_if
        self.defer = defer
        self.register_globally = register_globally
        # Filled in by ``_register_as_tool`` once a function is
        # attached. Held here so callers can introspect (``fn._agent_tool``)
        # without doing a registry lookup.
        self._agent_tool = None
        self._fn = None
        self._wrapper = None

        if fn is not None:
            # Used as @agentic_function without parentheses — fn is
            # already in hand, attach right now.
            self._attach(fn)

    def __call__(self, *args, **kwargs):
        # After attachment ``__call__`` is the Python-direct-invoke
        # path: forward to the wrapper so ``research("topic")`` works
        # like a regular function. The decorator-style entry path
        # (``@agentic_function(...)`` returning the partially-built
        # instance which is then called with the function object)
        # routes here too, but only once — when ``_fn`` is still None.
        if self._fn is not None:
            return self._wrapper(*args, **kwargs)
        # Decorator entry: the LHS is ``@agentic_function(...)``; the
        # call we're handling now is the one Python makes with the
        # decorated function as the single positional arg.
        fn = args[0]
        attached = self._attach(fn)
        # If Layer 1 gated us out, ``_attach`` returns the raw fn
        # unchanged. Return that so the module-level name points at a
        # plain callable rather than a half-built agentic_function.
        return attached if attached is not None else self

    def _attach(self, fn: Callable):
        """Bind ``fn`` to this instance, build wrapper, run gates, and
        optionally register as an AgentTool.

        Single attach path used by both no-parens (``__init__``) and
        with-parens (``__call__``) decorator forms. Returns ``fn``
        unchanged when Layer 1 (``available_if``) gates the function
        out — callers in ``__call__`` use that to short-circuit the
        return value; ``__init__`` ignores the return.

        Layer 1 (Claude Code "conditional import" equivalent): if the
        predicate is set and returns falsy (or raises), we skip the
        wrapper, both registries, and the AgentTool bridge. The raw
        fn is what callers get back, so module-level use degrades
        gracefully to "this is just a plain function" rather than a
        broken agentic instance.
        """
        if self.available_if is not None:
            try:
                if not self.available_if():
                    return fn
            except Exception:
                return fn
        self._fn = fn
        self._wrapper = self._make_wrapper(fn)
        functools.update_wrapper(self, fn)
        _registry[fn.__name__] = self
        if self.as_tool:
            self._register_as_tool()
        return None

    def __get__(self, obj, objtype=None):
        """Support instance methods."""
        if obj is None:
            return self
        return functools.partial(self._wrapper, obj)

    @property
    def spec(self) -> dict:
        """JSON-schema tool spec auto-generated from signature + docstring.

        Mirrors openprogram.functions.<name>.SPEC so an @agentic_function can be
        passed directly to runtime.exec(tools=[fn]). Runtime-injected params
        (runtime, exec_runtime, review_runtime) and any `hidden: True` entries
        in input_meta are excluded — they aren't LLM-controllable.
        """
        if self._fn is None:
            raise RuntimeError("agentic_function.spec accessed before a function was attached")
        return _build_agentic_tool_spec(self._fn, self.input_meta)

    def execute(self, **kwargs):
        """Call the wrapped function with LLM-provided kwargs.

        Used when this @agentic_function is exposed as a tool. Return value is
        converted to a string by the tool-loop driver if it isn't one already.
        """
        return self._wrapper(**kwargs)

    def _register_as_tool(self) -> None:
        """Bridge this @agentic_function into the shared AgentTool registry.

        Sits next to ``@function``-decorated tools in the same
        ``openprogram.functions._runtime._registry``, so the LLM can
        call this function via tool_call dispatch and so all 6 gating
        layers (available_if / toolset / mode preset / check_fn /
        deny rules / defer) apply uniformly.

        Delegates AgentTool construction + sidecar attach + register
        to ``_build_and_register_tool`` — the same helper ``@function``
        uses. The only piece unique to the agentic side is the
        ``_execute`` closure that funnels the LLM-passed kwargs through
        ``self._wrapper`` (the wrapper carries pre-invocation hooks,
        runtime injection, DAG entry/exit, and inner agent-loop
        spawning).

        Note: the file-local ``_registry`` (line 82) is kept and
        populated separately; ``spawn_program`` and the webui use it to
        look up the agentic_function *instance* (for ``.expose`` /
        ``.render_range`` / ``._fn`` / etc.) — that's distinct from
        looking up an ``AgentTool`` for dispatcher invocation, which
        is what the shared registry serves.
        """
        if self._fn is None or self._wrapper is None:
            return  # nothing to wrap yet

        # Lazy imports to avoid a hard cycle on package init —
        # @agentic_function may be imported before openprogram.functions
        # is fully constructed.
        from openprogram.agent.types import AgentToolResult
        from openprogram.functions._runtime import (
            _build_and_register_tool,
            _normalize_result,
            _effective_max_chars,
            _cache_key,
            _cache_get,
            _cache_set,
            DEFAULT_MAX_RESULT_CHARS,
            DEFAULT_HEAD_RATIO,
        )

        name = self.tool_name or self._fn.__name__
        # Reuse the dict-shape spec the legacy path already produced
        # so the parameter schema stays consistent (hidden params
        # filtered, type-hint extraction handled by the existing
        # ``_build_agentic_tool_spec`` helper).
        spec = _build_agentic_tool_spec(self._fn, self.input_meta)
        parameters = self.tool_parameters or spec.get("parameters") or {
            "type": "object", "properties": {}
        }
        description = (
            self.tool_description or spec.get("description") or self._fn.__name__
        )
        max_chars = self.max_result_chars or DEFAULT_MAX_RESULT_CHARS
        head_ratio = (
            self.head_ratio if self.head_ratio is not None else DEFAULT_HEAD_RATIO
        )
        persist_full = self.persist_full
        wrapper = self._wrapper
        use_cache = self.cache
        cache_ttl = self.cache_ttl
        exec_timeout = self.timeout

        async def _execute(call_id, args, cancel, on_update):
            # Funnel the LLM-passed kwargs through the wrapper (which
            # carries the agentic semantics) then normalise the return
            # value through the same truncation / persist-full path
            # @function uses. cache / timeout mirror @function's
            # semantics: memoize on (name, args); hard-kill after
            # ``timeout`` seconds with an is_error result.
            kwargs = dict(args or {})

            if use_cache:
                key = _cache_key(name, kwargs)
                hit = _cache_get(key)
                if hit is not None:
                    return hit

            async def _invoke():
                raw = wrapper(**kwargs)
                if inspect.iscoroutine(raw):
                    raw = await raw
                return raw

            if exec_timeout is not None:
                import asyncio
                import contextvars as _cv
                try:
                    if inspect.iscoroutinefunction(wrapper):
                        raw = await asyncio.wait_for(
                            _invoke(), timeout=exec_timeout)
                    else:
                        # A sync wrapper would block the event loop and
                        # make wait_for useless — run it in a thread,
                        # carrying the current Context so the body sees
                        # the calling task's ContextVars (_store /
                        # _call_id / _current_runtime).
                        loop = asyncio.get_running_loop()
                        ctx = _cv.copy_context()
                        raw = await asyncio.wait_for(
                            loop.run_in_executor(
                                None, lambda: ctx.run(wrapper, **kwargs)),
                            timeout=exec_timeout,
                        )
                except asyncio.TimeoutError:
                    from openprogram.providers.types import TextContent
                    return AgentToolResult(content=[TextContent(text=(
                        f"[error] function {name} timed out after "
                        f"{exec_timeout}s"
                    ))])
            else:
                raw = await _invoke()

            if isinstance(raw, AgentToolResult):
                result = raw
            else:
                result = _normalize_result(
                    raw,
                    call_id=call_id,
                    max_chars=_effective_max_chars(max_chars),
                    persist_full=persist_full,
                    head_ratio=head_ratio,
                )
            if use_cache:
                _cache_set(_cache_key(name, kwargs), result, cache_ttl)
            return result

        self._agent_tool = _build_and_register_tool(
            name=name,
            description=description,
            parameters=parameters,
            label=self.tool_label,
            execute=_execute,
            requires_approval=self.requires_approval,
            check_fn=self.check_fn,
            requires_env=self.requires_env,
            can_use=self.can_use,
            defer=self.defer,
            toolsets=self.toolset,
            unsafe_in=self.unsafe_in,
            register_globally=self.register_globally,
        )
        # Mark the AgentTool so the dispatcher can route an LLM-issued
        # call to this @agentic_function through the same runtime-block
        # rendering that the manual /run path uses, instead of the
        # collapsed tool-call card.
        try:
            setattr(self._agent_tool, "_is_agentic", True)
        except Exception:
            pass

    def _make_wrapper(self, fn: Callable) -> Callable:
        sig = inspect.signature(fn)

        if inspect.iscoroutinefunction(fn):
            return self._make_async_wrapper(fn, sig)
        return self._make_sync_wrapper(fn, sig)

    def _make_async_wrapper(self, fn: Callable, sig: inspect.Signature) -> Callable:
        self_ref = self
        expose = self.expose
        render_range = self.render_range
        system = self.system

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            # Cancel check / other pre-invocation hooks — may raise to abort.
            _run_pre_invocation_hooks()

            # Auto-inject runtime if needed
            new_args, new_kwargs, runtime_token, owns_runtime = _inject_runtime(sig, args, kwargs)

            import uuid as _uuid
            _pending_call_id = _uuid.uuid4().hex[:12]
            _started_at = time.time()

            bound = sig.bind(*new_args, **new_kwargs)
            bound.apply_defaults()
            bound_args = dict(bound.arguments)

            _append_function_call_entry(
                pending_id=_pending_call_id,
                function_name=fn.__name__,
                arguments=bound_args,
                expose=expose,
                render_range=render_range,
                started_at=_started_at,
                docstring=inspect.getdoc(fn) or "",
            )
            # Stamp ``_call_id`` so anything further down the call
            # tree (rt.exec → ModelCall.called_by, ask_user → user
            # Call.called_by) attributes its writes to this invocation.
            _call_token = _call_id.set(_pending_call_id)
            _system_saved = _apply_system(system, bound_args)
            output = None
            error = None
            status = "success"
            _usage_token = None
            try:
                from openprogram.metering.context import (
                    _current as _usage_cur, UsageContext,
                    current_usage_context as _cur_uctx,
                )
                _prev = _cur_uctx()
                _usage_token = _usage_cur.set(UsageContext(
                    call_kind="exec",
                    call_label=fn.__name__,
                    session_id=_prev.session_id,
                    parent_session_id=_prev.parent_session_id,
                    agent_id=_prev.agent_id,
                ))
            except Exception:
                pass
            # Prevent self-recursion (async variant) — same as sync above.
            from openprogram.agentic_programming.runtime import (
                _current_tool_policy as _ctp_async,
            )
            _self_name_async = getattr(self, "tool_name", None) or fn.__name__
            _prev_policy_async = _ctp_async.get(None) or {}
            _prev_deny_async = list(_prev_policy_async.get("deny") or [])
            _self_deny_policy_async = {**_prev_policy_async, "deny": _prev_deny_async + [_self_name_async]}
            _self_deny_token_async = _ctp_async.set(_self_deny_policy_async)
            try:
                output = await fn(*new_args, **new_kwargs)
                return output
            except CancelledError:
                error = "Cancelled by user"
                status = "error"
                raise
            except Exception as e:
                error = str(e)
                status = "error"
                raise
            finally:
                _ctp_async.reset(_self_deny_token_async)
                _restore_system(_system_saved)
                _update_function_call_exit(
                    pending_id=_pending_call_id,
                    output=output,
                    error=error,
                    status=status,
                    expose=expose,
                    started_at=_started_at,
                    ended_at=time.time(),
                )
                _call_id.reset(_call_token)
                if _usage_token is not None:
                    _usage_cur.reset(_usage_token)
                if runtime_token is not None:
                    _current_runtime.reset(runtime_token)
                if owns_runtime:
                    rt = bound.arguments.get("runtime")
                    if rt and hasattr(rt, 'close'):
                        rt.close()

        wrapper._is_agentic = True
        return wrapper

    def _make_sync_wrapper(self, fn: Callable, sig: inspect.Signature) -> Callable:
        self_ref = self
        expose = self.expose
        render_range = self.render_range
        system = self.system

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # Cancel check / other pre-invocation hooks — may raise to abort.
            _run_pre_invocation_hooks()

            # Auto-inject runtime if needed
            new_args, new_kwargs, runtime_token, owns_runtime = _inject_runtime(sig, args, kwargs)

            import uuid as _uuid
            _pending_call_id = _uuid.uuid4().hex[:12]
            _started_at = time.time()

            bound = sig.bind(*new_args, **new_kwargs)
            bound.apply_defaults()
            bound_args = dict(bound.arguments)

            _append_function_call_entry(
                pending_id=_pending_call_id,
                function_name=fn.__name__,
                arguments=bound_args,
                expose=expose,
                render_range=render_range,
                started_at=_started_at,
                docstring=inspect.getdoc(fn) or "",
            )
            _call_token = _call_id.set(_pending_call_id)
            # Apply the decorator's system= onto the injected runtime(s)
            # for the duration of this call so nested runtime.exec()
            # picks it up. Saved/restored so a caller's system survives.
            _system_saved = _apply_system(system, bound_args)
            output = None
            error = None
            status = "success"
            _usage_token = None
            try:
                from openprogram.metering.context import (
                    _current as _usage_cur, UsageContext,
                    current_usage_context as _cur_uctx,
                )
                _prev = _cur_uctx()
                _usage_token = _usage_cur.set(UsageContext(
                    call_kind="exec",
                    call_label=fn.__name__,
                    session_id=_prev.session_id,
                    parent_session_id=_prev.parent_session_id,
                    agent_id=_prev.agent_id,
                ))
            except Exception:
                pass
            # Prevent self-recursion: an agentic function's inner
            # runtime.exec must not see the function itself in the tool
            # list (otherwise the model can call wiki_agent inside
            # wiki_agent → infinite nesting). Push the function's own
            # name into the tool-policy deny for the duration of this call.
            from openprogram.agentic_programming.runtime import (
                _current_tool_policy as _ctp,
            )
            _self_name = getattr(self, "tool_name", None) or fn.__name__
            _prev_policy = _ctp.get(None) or {}
            _prev_deny = list(_prev_policy.get("deny") or [])
            _self_deny_policy = {**_prev_policy, "deny": _prev_deny + [_self_name]}
            _self_deny_token = _ctp.set(_self_deny_policy)
            try:
                output = fn(*new_args, **new_kwargs)
                return output
            except CancelledError:
                error = "Cancelled by user"
                status = "error"
                raise
            except Exception as e:
                error = str(e)
                status = "error"
                raise
            finally:
                _ctp.reset(_self_deny_token)
                _restore_system(_system_saved)
                _update_function_call_exit(
                    pending_id=_pending_call_id,
                    output=output,
                    error=error,
                    status=status,
                    expose=expose,
                    started_at=_started_at,
                    ended_at=time.time(),
                )
                _call_id.reset(_call_token)
                if _usage_token is not None:
                    _usage_cur.reset(_usage_token)
                if runtime_token is not None:
                    _current_runtime.reset(runtime_token)
                if owns_runtime:
                    rt = bound.arguments.get("runtime")
                    if rt and hasattr(rt, 'close'):
                        rt.close()

        wrapper._is_agentic = True
        return wrapper


_PY_TO_JSON_TYPE = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
    type(None): "null",
}


def _coerce_enum(values: list, json_type) -> list:
    """Coerce enum values to a JSON scalar type so type/enum agree.

    ``json_type`` is the schema ``type`` string ("integer"/"number"/
    "boolean"/"string"/...). Values that can't be coerced are left as-is
    (so a genuinely bad option surfaces rather than being silently
    dropped). Non-scalar / unknown types pass through unchanged.
    """
    def one(v):
        try:
            if json_type == "integer":
                return int(v)
            if json_type == "number":
                return float(v)
            if json_type == "boolean":
                if isinstance(v, bool):
                    return v
                return str(v).strip().lower() in ("true", "1", "yes")
            if json_type == "string":
                return str(v)
        except (TypeError, ValueError):
            return v
        return v
    return [one(v) for v in values]


def _type_to_json_schema(ann) -> dict:
    """Map a Python type annotation to a JSON Schema fragment."""
    import typing

    if ann is inspect.Parameter.empty:
        return {}

    origin = typing.get_origin(ann)
    args = typing.get_args(ann)

    # Optional[X] / Union[X, None]
    if origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            schema = _type_to_json_schema(non_none[0])
            return schema
        # Bare union — let the model send any; unconstrained
        return {}

    if ann in _PY_TO_JSON_TYPE:
        return {"type": _PY_TO_JSON_TYPE[ann]}

    if origin in (list, tuple):
        if args:
            return {"type": "array", "items": _type_to_json_schema(args[0])}
        return {"type": "array"}

    if origin is dict:
        return {"type": "object"}

    return {}


def _build_agentic_tool_spec(fn: Callable, input_meta: dict) -> dict:
    """Generate an OpenAI Responses-API-compatible tool spec from a Python fn."""
    sig = inspect.signature(fn)
    properties: dict[str, dict] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if name in _RUNTIME_PARAMS:
            continue
        meta = input_meta.get(name) or {}
        if meta.get("hidden"):
            continue

        schema = _type_to_json_schema(param.annotation) or {"type": "string"}
        description = meta.get("description")
        if description:
            schema["description"] = description
        elif meta.get("placeholder"):
            schema["description"] = f"e.g. {meta['placeholder']}"
        options = meta.get("options")
        if options:
            # Coerce enum values to match the param's declared JSON type.
            # UI ``options`` are often authored as display strings
            # (e.g. ["5","10","15"]) for a param annotated ``int`` — that
            # produces {"type":"integer","enum":["5",...]}, a type/enum
            # contradiction OpenAI strict-mode tool validation rejects
            # (HTTP 400), which breaks EVERY chat turn (all tool schemas
            # ship together). Normalise so the enum always agrees with
            # the type, regardless of how the harness wrote its options.
            schema["enum"] = _coerce_enum(list(options), schema.get("type"))

        properties[name] = schema
        if param.default is inspect.Parameter.empty:
            required.append(name)

    parameters: dict = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = required

    description = (fn.__doc__ or "").strip() or f"Call {fn.__name__}."
    return {
        "name": fn.__name__,
        "description": description,
        "parameters": parameters,
    }


def traced(fn):
    """Lightweight decorator that records function execution into the DAG.

    Unlike @agentic_function, this does NOT involve any LLM logic — it
    simply appends a placeholder code Call at entry and fills it in at
    exit, so the function appears in the execution graph. No-op when no
    ``_store`` is installed (standalone scripts).

    Usage:
        @traced
        def search_papers(query):
            ...
    """
    sig = inspect.signature(fn)

    def _enter(args, kwargs):
        import uuid as _uuid
        pending_call_id = _uuid.uuid4().hex[:12]
        started_at = time.time()

        try:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            bound_args = {k: v for k, v in bound.arguments.items()
                          if k not in ("self", "cls", "runtime", "callback")}
        except TypeError:
            bound_args = {}

        _append_function_call_entry(
            pending_id=pending_call_id,
            function_name=fn.__name__,
            arguments=bound_args,
            expose="io",
            render_range=None,
            started_at=started_at,
            docstring=inspect.getdoc(fn) or "",
        )
        call_token = _call_id.set(pending_call_id)
        return pending_call_id, started_at, call_token

    def _exit(pending_call_id, started_at, call_token, output, error, status):
        _update_function_call_exit(
            pending_id=pending_call_id,
            output=output,
            error=error,
            status=status,
            expose="io",
            started_at=started_at,
            ended_at=time.time(),
        )
        _call_id.reset(call_token)

    # Coroutine functions get an async wrapper — calling fn() without
    # awaiting would record the coroutine object's repr as the output
    # and a duration covering only coroutine creation.
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            pending_call_id, started_at, call_token = _enter(args, kwargs)
            output = None
            error = None
            status = "success"
            try:
                output = await fn(*args, **kwargs)
                return output
            except Exception as e:
                error = str(e)
                status = "error"
                raise
            finally:
                _exit(pending_call_id, started_at, call_token,
                      output, error, status)

        async_wrapper._is_traced = True
        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        pending_call_id, started_at, call_token = _enter(args, kwargs)
        output = None
        error = None
        status = "success"
        try:
            output = fn(*args, **kwargs)
            return output
        except Exception as e:
            error = str(e)
            status = "error"
            raise
        finally:
            _exit(pending_call_id, started_at, call_token,
                  output, error, status)

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

