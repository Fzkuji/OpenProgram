"""Execution memory access and durable source eligibility.

Modes constrain framework memory operations, not arbitrary filesystem tools.
Legacy administrative calls without an execution scope retain the global store.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from pathlib import Path
import re
from typing import Any


class MemoryPolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class MemoryPolicy:
    agent_id: str
    mode: str = 'read_write'
    read_spaces: tuple[str, ...] = ('legacy_global',)
    write_space: str = 'legacy_global'
    required: bool = False
    epoch: int = 0
    created_at: float = 0.0
    eligible: bool = True

    def snapshot(self) -> dict:
        return {'version': 1, **asdict(self)}


_current: ContextVar[MemoryPolicy | None] = ContextVar('memory_policy', default=None)
_request: ContextVar[Any] = ContextVar('memory_request', default=None)
_root: ContextVar[Path | None] = ContextVar('memory_root', default=None)


def current() -> MemoryPolicy | None:
    policy = _current.get()
    req = _request.get()
    raw = getattr(req, 'memory_policy_snapshot', None) if req else None
    if policy and raw and raw.get('agent_id') == policy.agent_id and not raw.get('eligible', False):
        return replace(policy, eligible=False)
    return policy


def _value(spec, key, default=None):
    return spec.get(key, default) if isinstance(spec, dict) else getattr(spec, key, default)


def resolve(agent_id: str, spec=None, override=None) -> MemoryPolicy:
    if spec is None:
        from openprogram.state.execution_facts import agent_registry
        manager = agent_registry()
        spec = manager.get(agent_id)
    raw = _value(spec, 'memory')
    if raw is None:
        raw = {'mode': 'read_write', 'read_spaces': ['legacy_global'], 'write_space': 'legacy_global'}
    if not isinstance(raw, dict):
        raise MemoryPolicyError('Invalid Agent memory configuration')
    mode = raw.get('mode', 'off')
    reads = raw.get('read_spaces', ['self'])
    write = raw.get('write_space', 'self')
    if mode not in {'off', 'read_only', 'read_write'} or not isinstance(reads, list) or any(s not in {'self', 'legacy_global'} for s in reads) or write not in {'self', 'legacy_global'}:
        raise MemoryPolicyError('Unsupported memory mode or space')
    if mode != 'off' and (not reads or (mode == 'read_write' and write not in reads)):
        raise MemoryPolicyError('Memory requires readable spaces and a readable write target')
    if 'self' in reads or write == 'self':
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', agent_id):
            raise MemoryPolicyError('Self memory requires a saved Agent identity')
    if override is not None:
        if not isinstance(override, dict) or set(override) != {'mode'} or override['mode'] not in {'off', 'read_only'}:
            raise MemoryPolicyError('Memory override may only narrow mode to off or read_only')
        if mode != 'off':
            mode = override['mode']
    return MemoryPolicy(agent_id, mode, tuple(dict.fromkeys(reads)), write,
                        bool(raw.get('required', False)), int(_value(spec, 'memory_policy_epoch', 0)),
                        float(_value(spec, 'created_at', 0) or 0), mode == 'read_write')


def from_snapshot(raw: dict) -> MemoryPolicy:
    try:
        if not isinstance(raw, dict) or raw.get('version') != 1:
            raise ValueError('unsupported version')
        return MemoryPolicy(agent_id=raw['agent_id'], mode=raw['mode'], read_spaces=tuple(raw['read_spaces']),
                            write_space=raw['write_space'], required=raw['required'], epoch=raw['epoch'],
                            created_at=raw.get('created_at', 0), eligible=raw['eligible'])
    except (KeyError, TypeError, ValueError) as exc:
        raise MemoryPolicyError('Invalid persisted memory policy') from exc


def space_path(policy: MemoryPolicy, space: str) -> Path:
    from openprogram.state.paths import get_memory_dir, get_state_dir
    state = get_state_dir()
    if space == 'legacy_global':
        return get_memory_dir()
    if space == 'self' and re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', policy.agent_id):
        return state / 'memory-spaces' / 'agents' / policy.agent_id
    raise MemoryPolicyError('Unsupported memory space')


def _live(policy: MemoryPolicy) -> MemoryPolicy:
    from openprogram.state.execution_facts import agent_registry
    manager = agent_registry()
    spec = manager.get(policy.agent_id)
    # Legacy unregistered caller configurations keep their historical behavior.
    if spec is None:
        if policy.epoch == 0 and policy.created_at == 0 and policy.read_spaces == ('legacy_global',):
            return policy
        raise MemoryPolicyError('Memory Agent is no longer available')
    live = resolve(policy.agent_id, spec)
    if live.created_at != policy.created_at:
        raise MemoryPolicyError('Memory Agent identity changed')
    return live


def check(*, write=False, policy=None, space=None) -> None:
    policy = policy or current()
    if policy is None:
        return
    from openprogram.state.memory import is_enabled
    if not is_enabled():
        raise MemoryPolicyError('Memory backend is disabled')
    live = _live(policy)
    if policy.mode == 'off' or live.mode == 'off':
        raise MemoryPolicyError('Memory is off for this execution')
    if write:
        if not policy.eligible or policy.mode != 'read_write' or live.mode != 'read_write' or live.epoch != policy.epoch or live.write_space != policy.write_space:
            raise MemoryPolicyError('Memory writing is not authorized for this execution')
        if space is not None and space != policy.write_space:
            raise MemoryPolicyError('Memory space is not writable')
    elif space is not None and (space not in policy.read_spaces or space not in live.read_spaces):
        raise MemoryPolicyError('Memory space is not readable')


def allowed(*, write=False) -> bool:
    try:
        check(write=write)
        return True
    except MemoryPolicyError:
        return False


@contextmanager
def scope(policy: MemoryPolicy | None, *, space: str | None = None):
    token = _current.set(policy)
    root_token = _root.set(space_path(policy, space) if policy and space else None)
    try:
        yield policy
    finally:
        _root.reset(root_token)
        _current.reset(token)


def selected_root() -> Path | None:
    policy = current()
    if policy is None:
        return _root.get()
    check()
    selected = _root.get()
    if selected is not None:
        for space in policy.read_spaces:
            if selected == space_path(policy, space):
                check(space=space)
                return selected
        raise MemoryPolicyError('Memory root is outside authorized spaces')
    space = policy.write_space if policy.write_space in policy.read_spaces else policy.read_spaces[0]
    check(space=space)
    return space_path(policy, space)


def primary_read_space() -> bool:
    """Whether this read is the policy's first readable space.

    A read that spans every space visits them one by one; what is read
    once per execution rather than once per space, such as the project
    layer, is read on the first visit only.
    """
    policy = current()
    selected = _root.get()
    if policy is None or selected is None or not policy.read_spaces:
        return True
    return selected == space_path(policy, policy.read_spaces[0])


@contextmanager
def commit_guard(root: Path):
    policy = current()
    if policy is None:
        yield
        return
    from openprogram.state.execution_facts import agent_registry
    manager = agent_registry()
    # Shared with Agent config mutations; the expensive model call is outside.
    lock = getattr(manager, 'configuration_lock', None)
    if lock is None:
        from contextlib import ExitStack
        @contextmanager
        def lock():
            with ExitStack() as stack:
                stack.enter_context(manager._lock)
                stack.enter_context(manager._file_lock(manager._index_file()))
                yield
    with lock():
        check(write=True)
        from .project_layer import active_root
        target = Path(root).resolve()
        if target != space_path(policy, policy.write_space).resolve() and target != active_root():
            raise MemoryPolicyError('Memory commit targets an unauthorized space')
        yield


def node_policy(message: dict) -> dict | None:
    if 'memory_policy' in message:
        return message['memory_policy'] if isinstance(message['memory_policy'], dict) else {}
    metadata = message.get('metadata') or {}
    if 'memory_policy' in metadata:
        return metadata['memory_policy'] if isinstance(metadata['memory_policy'], dict) else {}
    return None


def eligible(message: dict) -> bool:
    raw = node_policy(message)
    if raw is None:
        # A posted report records what the Agent already told the owner. It
        # has no execution of its own to carry a policy, and must not turn
        # the owner's next turn into one memory may not learn from.
        if message.get('always_on_report'):
            return True
        agent_id = message.get('agent_id')
        if not agent_id:
            return True
        from openprogram.state.execution_facts import agent_registry
        manager = agent_registry()
        spec = manager.get(agent_id)
        if spec is None or _value(spec, 'memory') is None:
            return True
        legacy = resolve(agent_id, spec)
        return legacy.mode == 'read_write' and legacy.write_space == 'legacy_global' and legacy.epoch <= 1
    try:
        policy = from_snapshot(raw)
        check(write=True, policy=policy)
        return policy.eligible and policy.mode == 'read_write'
    except MemoryPolicyError:
        return False


def stamp(req) -> dict:
    raw = getattr(req, 'memory_policy_snapshot', None)
    return {'memory_policy': raw} if raw is not None else {}


def restrict_history(policy: MemoryPolicy, history: list[dict]) -> MemoryPolicy:
    if any(not eligible(row) for row in history):
        return replace(policy, eligible=False)
    return policy


def narrow(policy: MemoryPolicy, original: MemoryPolicy) -> MemoryPolicy:
    """A recovered execution may lose permission, never acquire new permission."""
    if policy.agent_id != original.agent_id or policy.created_at != original.created_at:
        return replace(policy, mode='off', eligible=False)
    rank = {'off': 0, 'read_only': 1, 'read_write': 2}
    mode = min((policy.mode, original.mode), key=rank.__getitem__)
    reads = tuple(space for space in policy.read_spaces if space in original.read_spaces)
    if not reads:
        mode = 'off'
    return replace(policy, mode=mode, read_spaces=reads,
                   required=policy.required or original.required,
                   eligible=(policy.eligible and original.eligible and mode == 'read_write'
                             and policy.epoch == original.epoch
                             and policy.write_space == original.write_space))


def bind_history(req, history):
    policy = current()
    if policy is None:
        return
    policy = restrict_history(policy, history)
    _current.set(policy)
    req.memory_policy_snapshot = policy.snapshot()


@contextmanager
def request_scope(req):
    """Bind the request whose memory policy this execution enforces."""
    token = _request.set(req)
    try:
        yield
    finally:
        _request.reset(token)


def require_or_empty(exc):
    policy = current()
    if policy and policy.required and policy.mode != 'off':
        raise MemoryPolicyError('Required memory access failed') from exc
    if policy and policy.mode != 'off':
        req = _request.get()
        if req is not None:
            req.memory_degraded_reason = 'MEMORY_UNAVAILABLE'
        from openprogram.state.event_sink import emit_safe
        metadata = {'session': req.session_id} if req is not None else None
        emit_safe('memory.degraded', 'system', {'reason_code': 'MEMORY_UNAVAILABLE'}, metadata)


class ScopedBackend:
    """Stateless facade: all execution identity lives in ContextVars."""
    def __init__(self, backend):
        self.backend = backend

    @property
    def name(self):
        return self.backend.name

    @property
    def supports_execution_policy(self):
        return getattr(self.backend, 'supports_execution_policy', False)

    def is_available(self):
        return self.backend.is_available()

    def _read(self, method, *args, **kwargs):
        policy = current()
        if policy is None:
            return getattr(self.backend, method)(*args, **kwargs)
        if policy.mode == 'off':
            return ''
        blocks = []
        try:
            for space in policy.read_spaces:
                check(space=space)
                with scope(policy, space=space):
                    block = getattr(self.backend, method)(*args, **kwargs)
                check(space=space)
                if block:
                    blocks.append(block)
        except Exception as exc:
            require_or_empty(exc)
            return ''
        return '\n\n'.join(blocks)

    def system_prompt(self, **kwargs):
        return self._read('system_prompt', **kwargs)

    def search(self, *args, **kwargs):
        return self._read('search', *args, **kwargs)

    def write(self, *args, **kwargs):
        # The writer itself restores each durable source policy. This also
        # serves background callers after the foreground scope has ended.
        if current() is not None and not allowed(write=True):
            return None
        if current() is not None and not self.supports_execution_policy:
            from .backend import WriteFailure, MemoryWriteFailureCode
            return WriteFailure('Memory backend cannot enforce execution policy', reason_code=MemoryWriteFailureCode.MEMORY_PROVIDER_RESOLUTION_FAILED)
        return self.backend.write(*args, **kwargs)

    def reorganize(self, **kwargs):
        if current() is not None and not allowed(write=True):
            return {'status': 'disabled'}
        return self.backend.reorganize(**kwargs)

    def extract_before_discard(self, messages):
        if not allowed(write=True):
            return ''
        return self.backend.extract_before_discard(messages)

    def initialize(self, **kwargs):
        return self.backend.initialize(**kwargs)

    def shutdown(self):
        return self.backend.shutdown()


_owner_edit: ContextVar[bool] = ContextVar('memory_owner_edit', default=False)


@contextmanager
def owner_edit():
    """A person editing the shared memory store by hand, outside any execution.

    Settings and ``openprogram memory edit`` write the store a scope-less
    call writes (``legacy_global``, or the project layer over it). A
    background pass without a scope is not this: it never cites a stamped
    turn, which its own execution's pass writes.
    """
    token = _owner_edit.set(True)
    try:
        yield
    finally:
        _owner_edit.reset(token)


def owner_editing() -> bool:
    """Whether a person is editing memory by hand right now (``owner_edit``)."""
    return _owner_edit.get()


def automatic_writer() -> bool:
    """Whether the write under way is an automatic one: a writer or the
    organizer running for an execution, not a person's own edit."""
    return current() is not None and not _owner_edit.get()


def source_matches_execution(message):
    if not eligible(message):
        return False
    raw = node_policy(message)
    policy = current()
    if raw is None:
        return policy is None or (policy.write_space == 'legacy_global' and policy.epoch == 0)
    source = from_snapshot(raw)
    if policy is None and _owner_edit.get():
        # The person's own edit cites any conversation that wrote to the
        # store being edited; one an Agent kept for its own space stays there.
        return source.write_space == 'legacy_global'
    return policy is not None and (source.agent_id, source.epoch, source.created_at, source.write_space) == (policy.agent_id, policy.epoch, policy.created_at, policy.write_space)


def source_refusal(message) -> str:
    """Why ``source_matches_execution`` refuses this message, for a person."""
    if not eligible(message):
        return ('its conversation ran with memory off or read-only, or under an '
                'Agent memory setting that has changed since, so memory does not learn from it')
    raw = node_policy(message)
    if raw is not None and current() is None and from_snapshot(raw).write_space != 'legacy_global':
        return "it belongs to an Agent's own memory space, not this one"
    return 'it is outside the conversation this write may cite'


def guard_tool(function, *, write=False):
    from functools import wraps
    @wraps(function)
    def call(*args, **kwargs):
        import json
        policy = current()
        if policy is None:
            return function(*args, **kwargs)
        explicit = kwargs.pop('space', None)
        space = explicit or (policy.write_space if write else policy.read_spaces[0] if policy.read_spaces else None)
        try:
            check(write=write, space=space)
            with scope(policy, space=space):
                result = function(*args, **kwargs)
            check(write=write, space=space)
            return result
        except MemoryPolicyError as exc:
            message = str(exc)
            if explicit and message in ('Memory space is not readable', 'Memory space is not writable'):
                # A model that guessed a space must learn the authorized ones,
                # or it reads the denial as "memory is unavailable".
                spaces = (policy.write_space,) if write else policy.read_spaces
                message += (f": {explicit!r} is not authorized for this execution;"
                            f" authorized: {', '.join(spaces)}. Omit `space` to use the default.")
            return json.dumps({'ok': False, 'error': {'code': 'MEMORY_ACCESS_DENIED', 'message': message}})
    return call


def consume_sources(rows):
    """Persist a restriction when model input includes protected run output.

    Child tool tasks share the request object, so a restrictive result cannot
    be lost when the tool coroutine's local ContextVar is reset.
    """
    if not any(not eligible(row) for row in rows):
        return
    req = _request.get()
    if req is not None and getattr(req, 'memory_policy_snapshot', None):
        req.memory_policy_snapshot = {**req.memory_policy_snapshot, 'eligible': False}
        from openprogram.state.store.current import current_store
        db = current_store()
        reply_id = (getattr(req, 'user_msg_id', '') or '') + '_reply'
        if db.message_exists(req.session_id, reply_id):
            db.merge_node_metadata(req.session_id, reply_id, stamp(req))


def source_writable(session_id, node_id):
    if not session_id or not node_id:
        return False
    from openprogram.state.store.current import current_store
    try:
        rows = current_store().get_messages(session_id)
        return any(row.get('id') == node_id and eligible(row) for row in rows)
    except (AttributeError, KeyError, OSError, ValueError):
        return False


def prompt_for_agent(agent):
    from openprogram.state.memory import get_backend
    if current() is not None or _value(agent, 'memory') is None:
        return get_backend().system_prompt()
    with scope(resolve(_value(agent, 'id', 'main'), agent)):
        return get_backend().system_prompt()


def consume_node(session_id, node_id):
    from openprogram.state.store.current import current_store
    rows = [row for row in current_store().get_messages(session_id) if row.get('id') == node_id]
    consume_sources(rows or [{'memory_policy': {}}])


def readiness(agent_id: str, spec=None) -> dict:
    """Configuration-only readiness; never searches or creates a memory store."""
    policy = resolve(agent_id, spec)
    if policy.mode == 'off':
        return {'available': True, 'blocked': False, 'reason_code': None}
    try:
        from openprogram.state.memory import get_backend, is_enabled
        backend = get_backend()
        available = (is_enabled() and backend.is_available()
                     and getattr(backend, 'supports_execution_policy', False))
    except Exception:
        available = False
    return {'available': available, 'blocked': policy.required and not available,
            'reason_code': None if available else 'MEMORY_UNAVAILABLE'}
