"""Agent method configuration, execution, and existing tool registration."""
from __future__ import annotations
import asyncio
import functools
import inspect
import time
import types
from dataclasses import dataclass
from typing import Callable
from .call_state import (
    _RUNTIME_PARAMS,
    _run_pre_invocation_hooks,
    _inject_runtime,
    _current_runtime,
    _close_owned_runtime,
    _recursion_depth,
    _MAX_AGENTIC_RECURSION_DEPTH,
    _render_range_override,
    _forced_node_id,
    _apply_system,
    _restore_system,
    _update_function_call_exit,
)
from openprogram.host.cancellation import _current_cancel
from openprogram.runtime.context.scope import default_expose
import logging

_log = logging.getLogger(__name__)

class _ToolCancellation:
    """A tool deadline stops its own work while observing parent cancellation."""
    def __init__(self, parent, timeout):
        import threading
        self.parent = parent
        self.local = threading.Event()
        self.deadline = time.monotonic() + timeout
        self.timeout = timeout
        self.node_id = None

    def expired(self):
        return (time.monotonic() >= self.deadline
                and not (self.parent is not None and self.parent.is_set()))

    def set(self):
        self.local.set()

    def is_set(self):
        return self.local.is_set() or bool(self.parent is not None and self.parent.is_set())


@dataclass
class MethodOptions:
    expose: str | None = None
    render_range: dict | None = None
    input: dict | None = None
    capture_io: bool | None = None
    system: str | None = None
    tool: bool = False
    as_tool: bool | None = None
    resumable: bool = False
    name: str | None = None
    description: str | None = None
    parameters: dict | None = None
    label: str | None = None
    toolset: tuple = ()
    unsafe_in: tuple = ()
    check_fn: Callable | None = None
    requires_env: tuple = ()
    can_use: Callable | None = None
    max_result_weight: int | None = None
    requires_approval: object = None
    cache: bool = False
    cache_ttl: float = 300.0
    timeout: float | None = None
    available_if: Callable | None = None
    defer: bool = False
    register_globally: bool = True
    tool_visible: bool = True

    def __post_init__(self):
        self.as_tool = self.tool if self.as_tool is None else self.as_tool
        self.capture_io = self.as_tool if self.capture_io is None else self.capture_io
        self.expose = (default_expose() if self.as_tool else 'full') if self.expose is None else self.expose
        if self.expose not in ('io', 'llm', 'full', 'hidden'):
            raise ValueError(f'Invalid expose value: {self.expose!r}')
        for key in ('toolset', 'unsafe_in', 'requires_env'):
            setattr(self, key, tuple(getattr(self, key)))
        self.input_meta = self.input or {}
        for key in ('name', 'description', 'parameters', 'label'):
            setattr(self, 'tool_' + key, getattr(self, key))
        self._agent_tool = self._fn = self._wrapper = None


def method_options(options=None):
    return options if options is not None and not isinstance(options, dict) else MethodOptions(**(options or {}))


def wrap_agent_method(fn, options=None):
    options = method_options(options)
    if options.resumable and inspect.iscoroutinefunction(fn):
        raise ValueError('Resumable functions require synchronous explicit steps')
    return _make_wrapper(options, fn)


def register_agent_method(bound_fn, options=None):
    options = method_options(options)
    # Preserve the public bound signature. Unwrapping a method wrapper here
    # can reintroduce the receiver into the model tool schema.
    options._fn = bound_fn
    options._wrapper = bound_fn
    register_method(options)
    target = bound_fn.__func__ if inspect.ismethod(bound_fn) else bound_fn
    for key, value in vars(options).items():
        setattr(target, key, value)
    source = inspect.unwrap(bound_fn)
    owner = getattr(bound_fn, "_agent_owner", None) or getattr(bound_fn, "__self__", None)
    if owner is not None and inspect.isfunction(source):
        parameters = list(inspect.signature(source).parameters)
        if parameters and parameters[0] == "self":
            source = types.MethodType(source, owner)
        elif parameters and parameters[0] == "cls":
            source = types.MethodType(source, owner if inspect.isclass(owner) else type(owner))
    target._fn = source
    schema = _build_agent_tool_spec(options._fn, options.input_meta)
    target.spec = {
        "name": options.tool_name or schema["name"],
        "description": options.tool_description or schema.get("description", ""),
        "parameters": options.tool_parameters or schema.get("parameters", {}),
    }
    target.execute = bound_fn
    target._is_agent_method = True
    if options._agent_tool is not None:
        options._agent_tool._python_callable = bound_fn
        options._agent_tool._method_options = options
    return options._agent_tool

def register_method(self) -> None:
    """Register a configured method in the existing shared tool registry."""
    if not self.as_tool:
        return
    if self.available_if is not None:
        try:
            if not self.available_if():
                return
        except Exception:
            return
    if self._fn is None or self._wrapper is None:
        return  # nothing to wrap yet

    # Lazy imports to avoid a hard cycle on package init —
    # Agent methods may be imported before openprogram.programs
    # is fully constructed.
    from openprogram.models.providers.types import AgentToolResult
    from openprogram.programs._host.execution import (
        invoke_callable,
        timeout_tool_result,
        _normalize_result,
    )
    from openprogram.programs._host.runtime import (
        _build_and_register_tool,
        _cache_key,
        _cache_get,
        _cache_set,
    )

    name = self.tool_name or self._fn.__name__
    # Reuse the dict-shape spec the legacy path already produced
    # so the parameter schema stays consistent (hidden params
    # filtered, type-hint extraction handled by the existing
    # ``_build_agent_tool_spec`` helper).
    spec = _build_agent_tool_spec(self._fn, self.input_meta)
    parameters = self.tool_parameters or spec.get("parameters") or {
        "type": "object", "properties": {}
    }
    description = (
        self.tool_description or spec.get("description") or self._fn.__name__
    )
    result_cap = self.max_result_weight
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
        if exec_timeout is not None:
            cancel = _ToolCancellation(cancel, exec_timeout)
        cancel_token = (
            _current_cancel.set(cancel) if cancel is not None else None
        )
        tool_call_token = None
        identity_consumed_token = None
        try:
            from openprogram.programs._host.runtime import (
                _current_tool_call_id,
                _tool_call_identity_consumed,
            )

            tool_call_token = _current_tool_call_id.set(call_id)
            identity_consumed_token = _tool_call_identity_consumed.set(False)
        except ImportError:
            # ContextVar.set cannot raise; only the host runtime import can be missing,
            # and then the tool runs without a bound call identity.
            _log.debug("tool call identity not bound for %s", call_id, exc_info=True)

        try:
            if use_cache:
                key = _cache_key(name, kwargs)
                hit = _cache_get(key)
                if hit is not None:
                    return hit

            try:
                raw = await invoke_callable(
                    wrapper, kwargs,
                    timeout=exec_timeout,
                    is_async=inspect.iscoroutinefunction(wrapper),
                    # No-timeout sync still runs on the loop thread.
                    run_sync_in_executor=False,
                )
            except asyncio.TimeoutError:
                if exec_timeout is None:
                    raise
                setter = getattr(cancel, "set", None)
                if callable(setter):
                    setter()
                pending = cancel.node_id if isinstance(cancel, _ToolCancellation) else None
                if pending:
                    _update_function_call_exit(
                        pending_id=pending,
                        output=None,
                        error=(
                            f"function {name} timed out after "
                            f"{exec_timeout}s"
                        ),
                        status="error",
                        expose=self.expose,
                        started_at=None,
                        ended_at=time.time(),
                    )
                return timeout_tool_result(name, exec_timeout)
        finally:
            if tool_call_token is not None:
                try:
                    _current_tool_call_id.reset(tool_call_token)
                except (ValueError, RuntimeError):
                    # ContextVar.reset raises ValueError for a token from another
                    # context and RuntimeError for one already spent.
                    _log.debug("tool call id token already spent or foreign", exc_info=True)
            if identity_consumed_token is not None:
                try:
                    _tool_call_identity_consumed.reset(identity_consumed_token)
                except (ValueError, RuntimeError):
                    # ContextVar.reset raises ValueError for a token from another
                    # context and RuntimeError for one already spent.
                    _log.debug("identity-consumed token already spent or foreign", exc_info=True)
            if cancel_token is not None:
                _current_cancel.reset(cancel_token)

        result = _normalize_result(raw, call_id=call_id, cap=result_cap)
        if use_cache and not result.is_error:
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
        expose=self.tool_visible,
        register_globally=self.register_globally,
    )
    # Mark the AgentTool so the dispatcher can route an LLM-issued
    # call to this Agent method through the same runtime-block
    # rendering that the manual /run path uses, instead of the
    # collapsed tool-call card.
    setattr(self._agent_tool, "_is_agent_method", True)
    setattr(self._agent_tool, "_resumable", self.resumable)
    setattr(self._agent_tool, "_source_module", self._fn.__module__)
    setattr(self._agent_tool, "_python_callable", self._wrapper)
    setattr(self._agent_tool, "_dag_expose", self.expose)

def _make_wrapper(self, fn: Callable) -> Callable:
    sig = inspect.signature(fn)

    if inspect.iscoroutinefunction(fn):
        return _make_async_wrapper(self, fn, sig)
    return _make_sync_wrapper(self, fn, sig)

def _call_setup(self, fn, sig, args, kwargs, stack):
    from .call_scope import CallScope
    from .runtime_scope import execution_scope, _record_agent_method
    _run_pre_invocation_hooks()
    # Establish ownership before injection so no provider is constructed
    # for a function that does not make a model request.
    explicit = sig.bind_partial(*args, **kwargs)
    runtime = next((explicit.arguments.get(p) for p in _RUNTIME_PARAMS
                    if explicit.arguments.get(p) is not None), None)
    stack.enter_context(execution_scope(runtime=runtime))
    new_args, new_kwargs, runtime_token, owned_runtime = _inject_runtime(sig, args, kwargs)
    if runtime_token is not None:
        stack.callback(_current_runtime.reset, runtime_token)
    if owned_runtime is not None:
        stack.callback(_close_owned_runtime, owned_runtime)
    bound = sig.bind(*new_args, **new_kwargs)
    bound.apply_defaults()
    name = self.tool_name or fn.__name__
    record_call = _record_agent_method.get() or self.resumable
    previous = _recursion_depth.get(None) or {}
    identity = fn
    depth = previous.get(identity, 0)
    if depth >= _MAX_AGENTIC_RECURSION_DEPTH:
        raise RecursionError(
            f"Agent method {name} exceeded max nesting depth "
            f"{_MAX_AGENTIC_RECURSION_DEPTH}")
    token = _recursion_depth.set({**previous, identity: depth + 1})
    stack.callback(_recursion_depth.reset, token)
    render_range = self.render_range if self.render_range is not None else _render_range_override.get()
    pending_id = None
    if not inspect.iscoroutinefunction(fn):
        from .durable_steps import current_function_node_id
        # A continuation reuses its durable node even after provider scope exits.
        if not _forced_node_id.get():
            pending_id = current_function_node_id()
    call = stack.enter_context(CallScope(
        (self.tool_name or f'{fn.__module__}.{fn.__qualname__}'),
        docstring=inspect.getdoc(fn) or '', arguments={key: value for key, value in bound.arguments.items() if key not in ("self", "cls")},
        expose=self.expose, render_range=render_range, capture_io=self.capture_io, pending_id=pending_id,
        record=record_call))
    signal = _current_cancel.get()
    if self.as_tool and isinstance(signal, _ToolCancellation) and signal.node_id is None:
        signal.node_id = call.id
    record_token = _record_agent_method.set(False)
    stack.callback(_record_agent_method.reset, record_token)
    stack.callback(_restore_system, _apply_system(self.system, bound.arguments))
    try:
        from openprogram.models.usage.context import _current, UsageContext, current_usage_context
        prev = current_usage_context()
        token = _current.set(UsageContext(
            call_kind="exec", call_label=fn.__name__, session_id=prev.session_id,
            parent_session_id=prev.parent_session_id, agent_id=prev.agent_id))
        stack.callback(_current.reset, token)
    except Exception:
        # Usage metering is optional: without it the call is simply not
        # labelled for accounting.
        _log.debug("usage context not set for %s", fn.__name__, exc_info=True)
    return call, new_args, new_kwargs

def _make_async_wrapper(self, fn: Callable, sig: inspect.Signature) -> Callable:
    from contextlib import ExitStack
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        from .call_scope import capture_suspended
        if capture_suspended.get():
            return await fn(*args, **kwargs)
        with ExitStack() as stack:
            call, new_args, new_kwargs = _call_setup(self, fn, sig, args, kwargs, stack)
            try:
                call.output = await fn(*new_args, **new_kwargs)
            except asyncio.CancelledError:
                signal = _current_cancel.get()
                if isinstance(signal, _ToolCancellation) and signal.expired():
                    raise TimeoutError(f"function {fn.__name__} timed out after {signal.timeout}s") from None
                raise
            return call.output
    wrapper._is_agent_method = True
    return wrapper

def _make_sync_wrapper(self, fn: Callable, sig: inspect.Signature) -> Callable:
    from contextlib import ExitStack
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from .call_scope import capture_suspended
        if capture_suspended.get():
            return fn(*args, **kwargs)
        with ExitStack() as stack:
            call, new_args, new_kwargs = _call_setup(self, fn, sig, args, kwargs, stack)
            if self.resumable:
                from .durable_steps import invoke
                call.output = invoke(fn, self.tool_name or fn.__name__, new_args, new_kwargs)
            else:
                call.output = fn(*new_args, **new_kwargs)
            return call.output
    wrapper._is_agent_method = True
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


def _build_agent_tool_spec(fn: Callable, input_meta: dict) -> dict:
    """Generate an OpenAI Responses-API-compatible tool spec from a Python fn."""
    sig = inspect.signature(fn)
    properties: dict[str, dict] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if name in _RUNTIME_PARAMS or name in ("self", "cls"):
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
