"""Own standalone execution resources without constructing a provider early."""
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from tempfile import TemporaryDirectory
import uuid

from .runtime.questions import QuestionsOperations
from openprogram.runtime.context.scope import _call_id, _forced_predecessor


class _LazyRuntime(QuestionsOperations):
    """Resolve the existing provider Runtime only when it is used."""
    def __init__(self):
        object.__setattr__(self, '_runtime', None)
        object.__setattr__(self, '_settings', {})

    def _resolve(self, model=None):
        if self._runtime is None:
            from openprogram.runtime.agent.runtime.factory import create_runtime
            if not model:
                from .runtime import shared
                options_var = getattr(shared, '_current_agent_options', None)
                options = options_var.get() if options_var is not None else None
                model = (options or {}).get('model')
            if not model:
                model = self._settings.get('model')
            from openprogram.models.providers.catalog.models import split_model_ref
            provider, model_id = split_model_ref(model)
            if provider and model_id:
                runtime = create_runtime(provider=provider, model=model_id)
            elif model:
                runtime = create_runtime(model=model)
            else:
                runtime = create_runtime()
            for key, value in self._settings.items():
                setattr(runtime, key, value)
            object.__setattr__(self, '_runtime', runtime)
        return self._runtime

    def can_ask(self):
        if self._runtime is not None:
            return self._runtime.can_ask()
        return super().can_ask()

    def ask(self, *args, **kwargs):
        if self._runtime is not None:
            return self._runtime.ask(*args, **kwargs)
        return super().ask(*args, **kwargs)

    @contextmanager
    def _active_runtime(self, model=None):
        from .call_state import _current_runtime
        runtime = self._resolve(model)
        token = _current_runtime.set(runtime)
        try:
            yield runtime
        finally:
            _current_runtime.reset(token)

    def exec(self, *args, **kwargs):
        with self._active_runtime(kwargs.get('model')) as runtime:
            return runtime.exec(*args, **kwargs)

    async def async_exec(self, *args, **kwargs):
        with self._active_runtime(kwargs.get('model')) as runtime:
            return await runtime.async_exec(*args, **kwargs)

    def __getattr__(self, name):
        if name in self._settings:
            return self._settings[name]
        if self._runtime is None:
            if name in ('system', 'last_usage', 'api_model', '_question_transport',
                        'on_stream', '_skills_config', '_skills_cache_key', 'api_key'):
                return None
            if name == 'last_blocks':
                return []
            if name == 'last_agent_iteration_count':
                return 0
            if name in ('usage_is_cumulative', 'has_session', '_closed'):
                return False
            if name in ('model', 'provider_id'):
                from .runtime.shared import _current_agent_options
                model = _current_agent_options.get().get('model') or self._settings.get('model')
                if model and (':' in model or '/' in model):
                    delimiter = ':' if ':' in model else '/'
                    provider, model_id = model.split(delimiter, 1)
                    return provider if name == 'provider_id' else model_id
                return (model or 'default') if name == 'model' else None
            if name == 'thinking_level':
                return 'off'
            raise AttributeError(name)
        return getattr(self._runtime, name)

    def __setattr__(self, name, value):
        self._settings[name] = value
        if self._runtime is not None:
            setattr(self._runtime, name, value)

    def close(self):
        if self._runtime is not None:
            self._runtime.close()


def _profile_model(runtime):
    """Whether a scope's runtime is already a model built from a profile.

    A host-supplied ``call=`` is not, and neither is the placeholder that
    has not resolved a provider: the run may never ask for one.
    """
    if isinstance(runtime, _LazyRuntime):
        return runtime._runtime is not None
    return getattr(runtime, "_call_fn", None) is None


def profile_model_in_use():
    """A request is about to go to a model built from the profile.

    A standalone run that began owned by the process is the profile
    owner's from here on: it now runs on that profile's credentials, so
    what the profile keeps for its owner is open to it. The request object
    is shared by everything the run started, so all of it sees the owner.
    """
    from .turn_request import current_turn_request
    request = current_turn_request.get()
    if request is not None and getattr(request, "_process_owned", False):
        from openprogram.runtime.permissions.authority import owner_principal_id
        request.principal_id = owner_principal_id()
        request._process_owned = False


@contextmanager
def execution_scope(runtime=None):
    """Reuse ambient resources; release only resources owned by this entry."""
    from .call_state import _close_owned_runtime, _current_runtime
    from openprogram.state.store import _store, SessionNodeWriter, SessionStore
    from .turn_request import TurnRequest, current_turn_request
    with ExitStack() as stack:
        current = runtime if runtime is not None else _current_runtime.get(None)
        if current is None:
            current = _LazyRuntime()
            stack.callback(_close_owned_runtime, current)
        if current is not _current_runtime.get(None):
            token = _current_runtime.set(current)
            stack.callback(_current_runtime.reset, token)
        writer = _store.get()
        if writer is None:
            directory = stack.enter_context(TemporaryDirectory(prefix='openprogram-execution-'))
            store = SessionStore(directory)
            stack.callback(store.close)
            session_id = uuid.uuid4().hex
            store.create_session(session_id, 'standalone', source='python')
            writer = SessionNodeWriter(store, session_id)
            token = _store.set(writer)
            stack.callback(_store.reset, token)
        if current_turn_request.get() is None:
            from openprogram.runtime.permissions.authority import standalone_owner_authority
            profile_model = _profile_model(current)
            request = TurnRequest(session_id=getattr(writer, "session_id", "standalone"), user_text='',
                                  agent_id='standalone', source='python',
                                  **standalone_owner_authority(profile_model=profile_model))
            # Told apart from a request some turn built: only this one may
            # still become the profile owner's (profile_model_in_use).
            request._process_owned = not profile_model
            token = current_turn_request.set(request)
            stack.callback(current_turn_request.reset, token)
        yield current


runtime_scope = execution_scope


_agent_owner = ContextVar("dag_agent_owner", default=None)
# A tool implementation owns its methods, but not the dispatching Agent's graph.
_agent_graph_owner = ContextVar("dag_graph_owner", default=None)
_parent_agent_writer = ContextVar("parent_agent_writer", default=None)
_record_agent_method = ContextVar("record_agent_method", default=True)


@contextmanager
def agent_scope(owner, *, request=False):
    """Give a nested Agent a graph without inheriting parent graph identities.

    Authority, runtime options and cancellation remain bound by the caller.
    A freshly dispatched tool belongs to the dispatching graph; its ordinary
    helpers do not. A different Agent invoked from its body owns a child graph.
    """
    from . import call_state as state
    from .call_scope import CallScope
    from openprogram.runtime.context import Context
    from openprogram.state.store import SessionNodeWriter, _store
    from openprogram.programs._host.runtime import (
        _current_tool_call_id, _current_tool_call_occurrence_id,
        _tool_call_identity_consumed,
    )
    current = _agent_owner.get()
    direct_tool = bool(state.current_tool_call_occurrence_id() or (
        state.current_tool_call_id() and not state.tool_call_identity_consumed()))
    new_owner = owner is not current and not (
        isinstance(owner, type) and isinstance(current, owner))
    parent_writer = _store.get()
    needs_graph = new_owner or (request and _agent_graph_owner.get() is not owner)
    isolate = needs_graph and not direct_tool and bool(state.current_call_id())
    with ExitStack() as stack:
        link = None
        if isolate and isinstance(parent_writer, SessionNodeWriter):
            owner_type = owner if isinstance(owner, type) else type(owner)
            role = getattr(owner, "_role_name", "") if not isinstance(owner, type) else ""
            label = role or f"agent/{owner_type.__module__}.{owner_type.__qualname__}"
            link = stack.enter_context(CallScope(label, capture_io=True))
            child_id = uuid.uuid4().hex
            parent_writer.store.create_session(
                child_id, getattr(getattr(owner, "_spec", None), "id", None) or "agent",
                source="agent", parent_session_id=parent_writer.session_id,
                parent_call_id=link.id,
            )
            parent_writer.update(link.id, metadata={"child_session_id": child_id})
            # The child's calls spend the conversation's credential (its
            # pin and binding), not one chosen for the child alone.
            from openprogram.models.usage.context import current_usage_context, usage_scope
            stack.enter_context(usage_scope(
                parent_session_id=current_usage_context().parent_session_id
                or parent_writer.session_id))
            from openprogram.state.store import _current_turn_id
            from openprogram.state.store.snapshot.checkpoint.helpers import _checkpoint_owner
            if _checkpoint_owner.get() is None:
                token = _checkpoint_owner.set((parent_writer, _current_turn_id.get()))
                stack.callback(_checkpoint_owner.reset, token)
            writer = SessionNodeWriter(parent_writer.store, child_id)
            for var, value in (
                (_parent_agent_writer, parent_writer),
                (_store, writer), (_call_id, None),
                (state._forced_node_id, None), (_forced_predecessor, None),
                (_current_tool_call_id, None), (_current_tool_call_occurrence_id, None),
                (_tool_call_identity_consumed, False),
            ):
                token = var.set(value)
                stack.callback(var.reset, token)
            ambient = Context.current()
            context = (ambient if ambient is not None else Context()).derive(
                store=writer, head_id=None, call_id=None, excluded_call_ids=())
            stack.enter_context(context.bind())
        if needs_graph and not direct_tool:
            token = _agent_graph_owner.set(owner)
            stack.callback(_agent_graph_owner.reset, token)
        token = _agent_owner.set(owner)
        stack.callback(_agent_owner.reset, token)
        token = _record_agent_method.set(new_owner or direct_tool)
        stack.callback(_record_agent_method.reset, token)
        yield link


_prepared_agent_call = ContextVar('prepared_agent_call', default=False)


@contextmanager
def prepared_agent_call():
    """Let Agent.__call__/arun use the scope already owned by that instance."""
    token = _prepared_agent_call.set(True)
    try:
        yield
    finally:
        _prepared_agent_call.reset(token)


@contextmanager
def agent_request_scope():
    """A standalone agent() invocation owns its graph, even inside a tool."""
    if _prepared_agent_call.get():
        token = _prepared_agent_call.set(False)
        try:
            yield None
        finally:
            _prepared_agent_call.reset(token)
    else:
        with agent_scope(object()) as link:
            yield link


def context_for_agent_graph(context):
    """Carry supplied parent content without re-importing its graph identity."""
    from openprogram.state.store import _store
    parent = _parent_agent_writer.get()
    source = context.store
    if (parent is not None and source is not None
            and source.store is parent.store and source.session_id == parent.session_id):
        return context.derive(store=_store.get(), head_id=None, call_id=None,
                              excluded_call_ids=())
    return context
