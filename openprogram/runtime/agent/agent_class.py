"""Configured agent entries without implicit conversation state."""
from __future__ import annotations

import inspect
import sys
from functools import wraps
from contextlib import contextmanager
from typing import Any

from openprogram.runtime.context import Context

_UNSET = object()


from .turn_api import TurnOperations


class Agent(TurnOperations):
    """Configure model calls and automatically scope ordinary subclass methods.

    Context stores explicit named content. Each independent invocation owns a
    separate execution session. Methods are not registered as model tools.
    """

    model = None
    effort = None
    instructions = None
    context = None
    tools = None
    runtime = None
    method_options = {}

    def __init__(self, model=_UNSET, instructions=_UNSET, context=_UNSET,
                 tools=_UNSET, runtime=_UNSET, *, effort=_UNSET, **options):
        for name, value in dict(model=model, instructions=instructions,
                                context=context, tools=tools, runtime=runtime,
                                effort=effort).items():
            if value is not _UNSET:
                setattr(self, name, value)
        if self.context is not None and not isinstance(self.context, Context):
            raise TypeError("Agent context must be a Context or None.")
        if self.context is not None:
            self.context = self.context.derive()
        from openprogram.runtime.agent.agent_function import agent
        allowed = set(inspect.signature(agent).parameters) - {"prompt"}
        unknown = set(options) - allowed
        if unknown:
            raise TypeError("Unsupported Agent options: " + ", ".join(sorted(unknown)))
        self._options = dict(options)
        self._method_tools = {}
        from openprogram.runtime.agent.agent_method import register_agent_method
        for name, method_config in self.method_options.items():
            as_tool = method_config.get("as_tool")
            enabled = method_config.get("tool", False) if as_tool is None else as_tool
            if not enabled and not method_config.get("resumable", False):
                continue
            method = getattr(self, name)
            bound = _instance_tool_callable(method)
            bound._agent_owner = getattr(method, "__self__", None)
            bound._agent_method_name = name
            bound._method_options = dict(method_config)
            bound.resumable = method_config.get("resumable", False)
            setattr(self, name, bound)
            if enabled:
                tool = register_agent_method(bound, method_config)
                bound.execute = bound
                if tool is not None:
                    self._method_tools[name] = tool


    def _effective_context(self, override=_UNSET):
        from .runtime_scope import context_for_agent_graph
        ambient = Context.current()
        result = ambient.derive() if ambient is not None else Context()
        if self.context is not None and override is not None:
            result = result.merge(context_for_agent_graph(self.context))
        if override is not _UNSET and override is not None:
            if not isinstance(override, Context):
                raise TypeError("Agent context must be a Context or None.")
            result = result.merge(context_for_agent_graph(override))
        return result.derive(call_id=ambient.call_id if ambient is not None else None,
                             excluded_call_ids=ambient.excluded_call_ids if ambient is not None else ())

    def _call_options(self, overrides):
        from openprogram.runtime.agent.runtime.shared import _current_agent_options
        ambient = _current_agent_options.get()
        options = dict(ambient)
        configured = dict(self._options, model=self.model, effort=self.effort,
                          tools=self.tools, runtime=self.runtime)
        options.update({key: value for key, value in configured.items() if value is not None})
        options.update({key: value for key, value in overrides.items() if value is not None})
        deny = list(dict.fromkeys([*(ambient.get("tools_deny") or []),
                                 *(self._options.get("tools_deny") or []),
                                 *(overrides.get("tools_deny") or [])]))
        if deny:
            options["tools_deny"] = deny
        if getattr(self, "_spec", None) is not None:
            from openprogram.runtime.agent import engine_link
            options["tools"] = engine_link.resolve_tools(self._spec.to_dict(),
                                                         override=options.get("tools"))
        return options

    def _role_site(self, frame):
        from openprogram.runtime.agent.workflow_site import capture_site, role_name
        site = capture_site(frame)
        if site is not None:
            role_name(self, site)
        return site

    def _record_turn(self, context, prompt, reply):
        target = self.context if context is _UNSET else context
        if isinstance(target, Context):
            target.record_turn(prompt, reply)

    def __call__(self, prompt: str | list[dict], **overrides) -> Any:
        """Run one synchronous agent call with explicit option overrides."""
        from openprogram.runtime.agent.agent_function import agent
        from openprogram.runtime.agent.runtime_scope import prepared_agent_call
        from openprogram.runtime.agent.workflow_site import bind_site
        site = self._role_site(sys._getframe(1))
        context = overrides.pop("context", _UNSET)
        instructions = overrides.pop("instructions", self.instructions)
        with _configuration_scope(self, context=context, instructions=instructions,
                                  runtime=overrides.get("runtime", _UNSET), request=True) as link, \
                prepared_agent_call(), bind_site(site):
            result = agent(prompt=prompt, **self._call_options(overrides))
            if link is not None:
                link.output = result
        self._record_turn(context, prompt, result)
        return result

    def choose(self, prompt: str, options: dict[str, str], *, context=_UNSET,
               model: str = "", effort: str = "", timeout_s: float | None = None) -> str:
        """Return one supplied option ID without tools or automatic re-picking."""
        if not isinstance(options, dict) or not options or any(
            not isinstance(key, str) or not key.strip() or not isinstance(label, str)
            for key, label in options.items()
        ):
            raise ValueError("Choice options must map non-empty string IDs to string labels.")
        from openprogram.runtime.agent.llm import llm
        from openprogram.runtime.agent.call_scope import managed_function
        from openprogram.runtime.agent.runtime.shared import (
            _current_agent_options, _current_response_format, _current_model_call_budget,
        )
        from openprogram.runtime.agent.workflow_site import bind_site
        site = self._role_site(sys._getframe(1))

        def choose(prompt, **options):
            with bind_site(site):
                return llm(prompt, **options)

        with _configuration_scope(self, context=context, request=True) as link:
            # A selection has its own output contract, independent of any
            # surrounding tool loop or structured-output repair defaults.
            token = _current_agent_options.set(dict(
                _current_agent_options.get(), response_format=None,
                tools=[], web_search=False, tool_choice="none",
            ))
            format_token = _current_response_format.set(None)
            budget_token = _current_model_call_budget.set(None)
            try:
                result = managed_function(choose, name="choose")(
                    prompt, choices={key: (key, label) for key, label in options.items()},
                    model=model, effort=effort, timeout_s=timeout_s,
                )
                if link is not None:
                    link.output = result
            finally:
                _current_model_call_budget.reset(budget_token)
                _current_response_format.reset(format_token)
                _current_agent_options.reset(token)
        self._record_turn(context, prompt, result)
        return result

    async def arun(self, prompt: str | list[dict], **overrides) -> Any:
        """Run one asynchronous agent call with explicit option overrides."""
        from openprogram.runtime.agent.agent_function import agent_async
        from openprogram.runtime.agent.runtime_scope import prepared_agent_call
        from openprogram.runtime.agent.workflow_site import bind_site
        site = self._role_site(sys._getframe(1))
        context = overrides.pop("context", _UNSET)
        instructions = overrides.pop("instructions", self.instructions)
        with _configuration_scope(self, context=context, instructions=instructions,
                                  runtime=overrides.get("runtime", _UNSET), request=True) as link, \
                prepared_agent_call(), bind_site(site):
            result = await agent_async(prompt, **self._call_options(overrides))
            if link is not None:
                link.output = result
        self._record_turn(context, prompt, result)
        return result

    @classmethod
    def from_spec(cls, spec, **overrides):
        """Use saved parameters, inheriting caller instructions when none are saved."""
        policy = spec.tools or {}
        options = dict(model=spec.model.id, effort=spec.thinking_effort,
                       instructions=spec.system_prompt or None)
        options.update(toolset=policy.get("toolset"), tools_deny=policy.get("disabled"))
        if policy.get("mode") == "none" or policy.get("enabled") is False:
            options["tools"] = []
        options.update(overrides)
        instance = cls(**options)
        instance._spec = spec
        return instance

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "instructions" not in vars(cls) and cls.__doc__:
            cls.instructions = inspect.cleandoc(cls.__doc__)
        merged_options = {}
        for base in reversed(cls.__mro__):
            for method_name, method_options in getattr(base, "method_options", {}).items():
                merged_options.setdefault(method_name, {}).update(method_options)
        cls.method_options = merged_options
        declarations = dict(vars(cls))
        for method_name in merged_options:
            if method_name not in declarations and hasattr(cls, method_name):
                declarations[method_name] = inspect.getattr_static(cls, method_name)
        for name, descriptor in declarations.items():
            if name.startswith("__") or name in TurnOperations.__dict__:
                continue
            kind = type(descriptor) if isinstance(descriptor, (staticmethod, classmethod)) else None
            fn = descriptor.__func__ if kind else descriptor
            if not inspect.isfunction(fn):
                continue
            if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn):
                continue
            if getattr(fn, "_is_agent_configured_method", False):
                fn = fn._agent_method_source
            elif getattr(fn, "_is_managed_function", False):
                fn = fn.__wrapped__
            if getattr(fn, "_is_agent_method", False):
                continue
            options = cls.method_options.get(name, {})
            from openprogram.runtime.agent.agent_method import wrap_agent_method
            wrapped = wrap_agent_method(fn, options)
            if kind is None:
                wrapped = _configured_method(wrapped)
            else:
                wrapped = _class_scoped_method(wrapped, cls)
            wrapped._is_agent_configured_method = True
            wrapped._agent_method_source = fn
            setattr(cls, name, kind(wrapped) if kind else wrapped)


@contextmanager
def _configuration_scope(instance, *, context=_UNSET, instructions=_UNSET, runtime=_UNSET,
                         request=False):
    from openprogram.runtime.agent.runtime_scope import execution_scope, agent_scope
    from openprogram.runtime.agent.runtime.shared import _current_instructions, _current_agent_options
    from openprogram.runtime.agent.call_state import _current_runtime
    runtime = instance.runtime if runtime is _UNSET else runtime
    instructions = instance.instructions if instructions is _UNSET else instructions
    token = _current_instructions.set(_current_instructions.get() if instructions is None else instructions)
    owned = None
    try:
        with agent_scope(instance, request=request) as link, instance._effective_context(context).bind():
            spec = getattr(instance, "_spec", None)
            if runtime is None and _current_runtime.get(None) is None and spec is not None and spec.model.provider:
                from openprogram.runtime.agent.runtime.factory import create_runtime
                owned = runtime = create_runtime(provider=spec.model.provider, model=spec.model.id or None)
            with execution_scope(runtime=runtime):
                defaults_token = _current_agent_options.set(instance._call_options({}))
                try:
                    yield link
                finally:
                    _current_agent_options.reset(defaults_token)
    finally:
        _current_instructions.reset(token)
        if owned is not None:
            owned.close()


def _instance_tool_callable(bound):
    """Keep tool metadata and executors private to one configured instance."""
    if inspect.iscoroutinefunction(bound):
        @wraps(bound)
        async def async_tool(*args, **kwargs):
            return await bound(*args, **kwargs)
        return async_tool
    @wraps(bound)
    def tool(*args, **kwargs):
        return bound(*args, **kwargs)
    return tool


def _passed_runtime(signature, instance, args, kwargs):
    """The runtime the caller handed to this call, by name or by position,
    so the scope around the method is the caller's and not a default one
    resolved from a profile."""
    from .call_state import _RUNTIME_PARAMS
    try:
        passed = signature.bind_partial(instance, *args, **kwargs).arguments
    except TypeError:
        # The call does not fit the method; the method itself reports that.
        return _UNSET
    return next((passed[name] for name in signature.parameters
                 if name in _RUNTIME_PARAMS and passed.get(name) is not None), _UNSET)


def _configured_method(fn):
    signature = inspect.signature(fn)
    if inspect.iscoroutinefunction(fn):
        @wraps(fn)
        async def async_method(self, *args, **kwargs):
            runtime = _passed_runtime(signature, self, args, kwargs)
            with _configuration_scope(self, runtime=runtime) as link:
                result = await fn(self, *args, **kwargs)
                if link is not None:
                    link.output = result
                return result
        return async_method
    @wraps(fn)
    def method(self, *args, **kwargs):
        runtime = _passed_runtime(signature, self, args, kwargs)
        with _configuration_scope(self, runtime=runtime) as link:
            result = fn(self, *args, **kwargs)
            if link is not None:
                link.output = result
            return result
    return method


def _class_scoped_method(fn, owner):
    from .runtime_scope import agent_scope
    if inspect.iscoroutinefunction(fn):
        @wraps(fn)
        async def async_method(*args, **kwargs):
            with agent_scope(owner) as link:
                result = await fn(*args, **kwargs)
                if link is not None:
                    link.output = result
                return result
        return async_method
    @wraps(fn)
    def method(*args, **kwargs):
        with agent_scope(owner) as link:
            result = fn(*args, **kwargs)
            if link is not None:
                link.output = result
            return result
    return method


__all__ = ["Agent"]
