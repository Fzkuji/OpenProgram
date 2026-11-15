"""Task-local call identity and recording shared by Python entry points."""
from __future__ import annotations

import asyncio
import functools
import inspect
import time
import threading
import uuid
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from openprogram.runtime.context.scope import _call_id
import logging

_log = logging.getLogger(__name__)

capture_suspended = ContextVar("openprogram_capture_suspended", default=False)


def execution_task_id():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return f'asyncio:{id(task)}' if task is not None else f'thread:{threading.get_ident()}'


class CallScope:
    def __init__(self, function_name, *, docstring='', arguments=None, expose='full',
                 render_range=None, capture_io=False, pending_id=None, context=None, record=True):
        self.record = record
        self.function_name = function_name
        self.docstring = docstring
        self.arguments = arguments or {}
        self.expose = expose
        self.render_range = render_range
        self.capture_io = capture_io
        self.id = pending_id
        self.context = context
        self.parent_id = ''
        self.output = None
        self.error = None
        self.status = 'running'
        self._stack = ExitStack()

    def __enter__(self):
        from . import call_state as f
        from openprogram.runtime.context.model import Context
        self.parent_id = f.current_call_id()
        if not self.record:
            self.id = self.parent_id
            self.context = self.context if self.context is not None else Context.current()
            return self
        occurrence = f.current_tool_call_occurrence_id()
        tool_id = f.current_tool_call_id()
        identity = occurrence or (tool_id if not f.tool_call_identity_consumed() else '')
        if self.id is None:
            if identity and self.parent_id:
                self.id = f.tool_node_id(self.parent_id, identity)
            elif f._forced_node_id.get() and not self.parent_id:
                self.id = f._forced_node_id.get()
            else:
                self.id = uuid.uuid4().hex[:12]
        self.started_at = time.time()
        parent = self.context if self.context is not None else Context.current()
        if parent is None:
            parent = Context()
        excluded = set(parent.excluded_call_ids)
        from openprogram.state.store import _store
        from openprogram.state.store.session.session_node_writer import SessionNodeWriter
        writer = _store.get()
        task_id = execution_task_id()
        if isinstance(writer, SessionNodeWriter):
            for node in writer.load().nodes.values():
                metadata = node.metadata or {}
                if (node.caller == self.parent_id and node.id != self.id
                        and metadata.get('task_id') not in (None, task_id)
                        and metadata.get('status') in ('running', 'pending', 'streaming')):
                    excluded.add(node.id)
        try:
            f._append_function_call_entry(
                pending_id=self.id, function_name=self.function_name,
                arguments=self.arguments if self.capture_io else {}, expose=self.expose,
                render_range=self.render_range, started_at=self.started_at,
                docstring=self.docstring, structural=not self.capture_io)
            if isinstance(writer, SessionNodeWriter) and self.expose != 'hidden':
                writer.update(self.id, metadata={'task_id': task_id})
            if occurrence or tool_id:
                from openprogram.programs._host.runtime import (
                    _current_tool_call_occurrence_id, _tool_call_identity_consumed)
                token = _tool_call_identity_consumed.set(True)
                self._stack.callback(_tool_call_identity_consumed.reset, token)
                if occurrence:
                    token = _current_tool_call_occurrence_id.set(None)
                    self._stack.callback(_current_tool_call_occurrence_id.reset, token)
            token = _call_id.set(self.id)
            self._stack.callback(_call_id.reset, token)
            self.context = parent.derive(call_id=self.id, excluded_call_ids=excluded)
            self._stack.enter_context(self.context.bind())
            return self
        except BaseException:
            self._stack.close()
            raise

    def __exit__(self, exc_type, exc, tb):
        if not self.record:
            return False
        from . import call_state as f
        from openprogram.host.cancellation import CancelledError
        from .durable_steps import FunctionSuspended
        if isinstance(exc, FunctionSuspended):
            self.status = 'paused'
        elif isinstance(exc, (asyncio.CancelledError, CancelledError)):
            self.status, self.error = 'cancelled', 'Cancelled by user'
        elif exc is not None:
            self.status, self.error = 'error', str(exc)
        elif self.status == 'running':
            self.status = 'completed'
        try:
            f._update_function_call_exit(
                pending_id=self.id, output=self.output if self.capture_io else None,
                error=self.error, status=self.status, expose=self.expose,
                started_at=self.started_at, ended_at=time.time())
        finally:
            self._stack.close()
        if (self.capture_io and self.status == 'completed'
                and isinstance(self.output, dict) and 'target' in self.output):
            # A step that acted on a native app window: the chat preview
            # follows it the way it follows web pages.
            try:
                from openprogram.host.resources.native_window import note_operated_window
                note_operated_window(self.output)
            except Exception:
                # The window preview is optional; a failure must not fail the step.
                _log.debug("operated window not noted for call %s", self.id, exc_info=True)
        return False


def managed_function(fn, *, context_factory=None, name=None, expose="full",
                     render_range=None, capture_io=False, input=None, entry_owned=True):
    """Bind a structural call scope before a synchronous or async body runs.

    Generators remain unwrapped because creation does not execute their body.
    ``context_factory`` receives the invocation's positional and keyword values.
    Ambient-only helpers reuse the active call without recording another node.
    """
    if isinstance(fn, (staticmethod, classmethod)):
        return type(fn)(managed_function(fn.__func__, context_factory=context_factory, name=name,
                                               expose=expose, render_range=render_range,
                                               capture_io=capture_io, input=input,
                                               entry_owned=entry_owned))
    if (not inspect.isfunction(fn) or inspect.isgeneratorfunction(fn)
            or inspect.isasyncgenfunction(fn)
            or any(getattr(fn, marker, False) for marker in
                   ('_is_managed_function', '_is_agent_method', '_is_traced'))):
        return fn
    label = name or f'{fn.__module__}.{fn.__qualname__}'

    sig = inspect.signature(fn)

    def capture_enabled():
        if capture_suspended.get():
            return False
        if entry_owned:
            return True
        from openprogram.state.store import _store
        from .call_state import current_call_id
        return _store.get() is not None and bool(current_call_id())

    def scope(args, kwargs, context):
        from .call_state import current_call_id
        arguments = {}
        if capture_io:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            arguments = {key: value for key, value in bound.arguments.items()
                         if key not in ('self', 'cls')}
        return CallScope(label, docstring=inspect.getdoc(fn) or '', context=context,
                         arguments=arguments, expose=expose, render_range=render_range,
                         capture_io=capture_io,
                         record=not bool(current_call_id()))

    @contextmanager
    def invocation_scope(args, kwargs):
        from .runtime_scope import execution_scope
        from .call_state import (
            _RUNTIME_PARAMS, _inject_runtime, _current_runtime, _close_owned_runtime,
        )
        context = context_factory(args, kwargs) if context_factory else None
        explicit = sig.bind_partial(*args, **kwargs)
        runtime = next((explicit.arguments.get(name) for name in _RUNTIME_PARAMS
                        if explicit.arguments.get(name) is not None), None)
        with ExitStack() as stack:
            if context is not None:
                stack.enter_context(context.bind())
            stack.enter_context(execution_scope(runtime=runtime))
            new_args, new_kwargs, token, owned = _inject_runtime(sig, args, kwargs)
            if token is not None:
                stack.callback(_current_runtime.reset, token)
            if owned is not None:
                stack.callback(_close_owned_runtime, owned)
            call = stack.enter_context(scope(new_args, new_kwargs, context))
            yield call, new_args, new_kwargs

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            if not capture_enabled():
                return await fn(*args, **kwargs)
            from .call_state import _run_pre_invocation_hooks
            _run_pre_invocation_hooks()
            with invocation_scope(args, kwargs) as (call, new_args, new_kwargs):
                call.output = await fn(*new_args, **new_kwargs)
                return call.output
    else:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not capture_enabled():
                return fn(*args, **kwargs)
            from .call_state import _run_pre_invocation_hooks
            _run_pre_invocation_hooks()
            with invocation_scope(args, kwargs) as (call, new_args, new_kwargs):
                call.output = fn(*new_args, **new_kwargs)
                return call.output
    wrapper._is_managed_function = True
    return wrapper
