"""Bind a turn's memory policy for the length of its execution.

``openprogram.state.memory.policy`` defines the policy (modes, spaces,
eligibility) and the scopes it is read through. Which policy a turn runs
under depends on the session's Agent binding, the saved Agent and the
rows a resumed turn already wrote, which only the conversation layer
knows, so entering that scope lives here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from functools import wraps

from openprogram.state.memory import policy as memory_policy
from openprogram.state.memory.policy import MemoryPolicyError


@contextmanager
def execution(req, on_event=None, *, resume_rows=()):
    from openprogram.runtime.agent_registry import manager
    from openprogram.runtime.conversation import session_config
    loader = getattr(session_config, 'load_agent_session_binding', None)
    try:
        binding = loader(req.session_id) if loader else {}
    except (ValueError, TypeError) as exc:
        raise MemoryPolicyError("Invalid session memory binding") from exc
    if binding:
        if getattr(req, 'profile_snapshot', None) is None:
            req.profile_snapshot = binding.get('profile_snapshot')
        trial_override = binding.get('memory_policy_override')
        if trial_override:
            requested = getattr(req, 'memory_policy_override', None) or trial_override
            req.memory_policy_override = {'mode': 'off' if requested.get('mode') == 'off' or trial_override.get('mode') == 'off' else 'read_only'}
    spec = getattr(req, 'profile_snapshot', None) or manager.get(req.agent_id)
    policy = memory_policy.resolve(req.agent_id, spec, getattr(req, 'memory_policy_override', None))
    previous = getattr(req, 'memory_policy_snapshot', None)
    if previous is not None:
        policy = memory_policy.narrow(policy, memory_policy.from_snapshot(previous))
    for row in resume_rows:
        raw = memory_policy.node_policy(row)
        if raw is not None:
            policy = memory_policy.narrow(policy, memory_policy.from_snapshot(raw))
        elif not memory_policy.eligible(row):
            policy = replace(policy, eligible=False)
    outer = memory_policy.current()
    if outer is not None and not outer.eligible:
        policy = replace(policy, eligible=False)
    req.memory_policy_snapshot = policy.snapshot()
    req.memory_degraded_reason = None
    with memory_policy.scope(policy):
        if policy.mode != 'off':
            try:
                memory_policy.check()
                from openprogram.state.memory import get_backend
                backend = get_backend()
                if not getattr(backend, 'is_available', lambda: True)():
                    raise MemoryPolicyError('Memory backend is unavailable')
                if (policy.epoch > 0 or 'self' in policy.read_spaces) and not getattr(backend, 'supports_execution_policy', False):
                    raise MemoryPolicyError('Memory backend does not support Agent spaces')
            except Exception as exc:
                if policy.required:
                    raise MemoryPolicyError('Required memory is unavailable') from exc
                req.memory_degraded_reason = 'MEMORY_UNAVAILABLE'
                policy = replace(policy, mode='off', eligible=False)
                req.memory_policy_snapshot = policy.snapshot()
                from openprogram.runtime.events import emit_safe
                emit_safe('memory.degraded', 'system', {'reason_code': 'MEMORY_UNAVAILABLE'}, {'session': req.session_id})
                if on_event:
                    on_event({'type': 'chat_response', 'data': {'type': 'memory_degraded', 'reason_code': 'MEMORY_UNAVAILABLE', 'session_id': req.session_id}})
        with memory_policy.scope(policy):
            yield policy


def scoped_execution(function):
    @wraps(function)
    def run(request_or_continuation, *args, **kwargs):
        req = getattr(request_or_continuation, 'request', request_or_continuation)
        with memory_policy.request_scope(req):
            try:
                resume_rows = []
                if req is not request_or_continuation or (kwargs.get('execution_context') or {}).get('restart_initial'):
                    from openprogram.runtime.context.session_db import default_db
                    if req is not request_or_continuation:
                        user_id = request_or_continuation.user_message_id
                        reply_id = request_or_continuation.assistant_message_id
                    else:
                        user_id = req.user_msg_id
                        reply_id = (user_id or '') + '_reply'
                    resume_rows = [row for row in default_db().get_messages(req.session_id)
                                   if row.get('id') in {user_id, reply_id}]
                with execution(req, kwargs.get('on_event'), resume_rows=resume_rows):
                    return function(request_or_continuation, *args, **kwargs)
            except MemoryPolicyError as exc:
                from openprogram.runtime.conversation.turn_runtime.types import TurnResult
                return TurnResult(final_text='', user_msg_id=req.user_msg_id or '', assistant_msg_id='',
                                  failed=True, error=str(exc), error_reason='unknown', error_retryable=False,
                                  error_kind='memory_unavailable')
    return run


__all__ = ["execution", "scoped_execution"]
