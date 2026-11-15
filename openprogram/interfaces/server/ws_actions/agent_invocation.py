"""Validate first-message Agent configuration at the Web transport boundary."""
from __future__ import annotations

from copy import deepcopy
import re


class AgentInvocationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def prepare_agent_invocation(cmd: dict) -> dict | None:
    """Resolve editable input without changing registry or session state."""
    explicit = any(key in cmd for key in ('agent_id', 'agent_config', 'agent_trial', 'agent_overrides'))
    if not explicit:
        return None
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.agent_registry import manager
    from openprogram.interfaces.server.routes.catalog.agents import _validated_patch

    agent_id = cmd.get('agent_id')
    if not isinstance(agent_id, str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,39}', agent_id):
        raise AgentInvocationError('invalid_agent_config', 'A valid Agent ID is required')
    trial = cmd.get('agent_trial', False)
    if not isinstance(trial, bool):
        raise AgentInvocationError('invalid_agent_config', 'agent_trial must be a boolean')
    if ('agent_config' in cmd) != trial:
        raise AgentInvocationError('invalid_agent_config', 'Draft configuration requires agent_trial=true')
    row = default_db().get_session(cmd.get('session_id')) if cmd.get('session_id') else None
    if row:
        # New chat creates the conversation's row before anything is said, so
        # a row without messages is still a draft: its first message binds the
        # Agent picked in the composer. A conversation with messages, or a
        # trial, keeps the Agent it has.
        has_messages = bool(default_db().get_messages(cmd['session_id']))
        if (has_messages or row.get('agent_trial')) and row.get('agent_id') != agent_id:
            raise AgentInvocationError('agent_session_bound', 'This conversation is already bound to another Agent')
        if trial and not row.get('agent_trial'):
            raise AgentInvocationError('agent_session_bound', 'Start a new conversation to try an Agent configuration')
        if not row.get('agent_trial') and has_messages:
            if trial or 'agent_overrides' in cmd:
                raise AgentInvocationError('agent_session_bound', 'Start a new conversation to use a different Agent configuration')
            return None
    agent = manager.get(agent_id)
    if agent is None:
        raise AgentInvocationError('agent_not_found', 'The selected Agent no longer exists')
    profile = deepcopy(agent.to_dict())
    try:
        draft_patch = _validated_patch(cmd['agent_config']) if trial else {}
        if 'memory' in draft_patch:
            draft_patch['memory'] = manager.validate_memory_config({
                **profile.get('memory', {}), **draft_patch['memory'],
            })
        profile.update(draft_patch)
        overrides = cmd.get('agent_overrides', {})
        if not isinstance(overrides, dict) or set(overrides) - {'model', 'thinking_effort'}:
            raise ValueError('agent_overrides only accepts model and thinking_effort')
        profile.update(_validated_patch(overrides))
    except (ValueError, TypeError) as exc:
        raise AgentInvocationError('invalid_agent_config', str(exc)) from exc
    if row and row.get('agent_trial'):
        # A trial remains read-only even if the transcript is cleared. A repeat
        # of the same first-message intent is safe after a lost ACK; a changed
        # configuration must use a new session.
        stored = row.get('agent_profile_snapshot') or {}
        changed = (trial and any(stored.get(key) != value
                                for key, value in draft_patch.items()))
        if changed or overrides:
            raise AgentInvocationError('agent_session_bound', 'Start a new conversation to use a different Agent configuration')
        return None
    resolved_model = deepcopy(profile.get('model') or {})
    if not resolved_model.get('provider') and not resolved_model.get('id'):
        # Resolve inheritance before session creation consumes the one-shot
        # global picker pin. Keep the editable profile's inheritance intact.
        from openprogram.interfaces.server import server
        runtime = server.runtime_management
        provider, model_id = (
            (server._user_pinned_provider, server._user_pinned_model)
            if server._user_pinned_provider and server._user_pinned_model
            else runtime._last_used_chat_model()
        )
        if not runtime._default_is_enabled(provider, model_id):
            raise AgentInvocationError('agent_model_unavailable', 'No model has been used yet. Choose a model before starting this conversation')
        resolved_model = {'provider': provider, 'id': model_id}
    # Server-owned identity, timestamps and policy epoch cannot be supplied by
    # the renderer. The existing runtime still applies global capability gates.
    return {'agent_id': agent_id, 'profile_snapshot': profile, 'agent_trial': trial, 'resolved_model': resolved_model}


def persist_agent_invocation(session_id: str, invocation: dict, conv: dict,
                             branch_id: str | None = None) -> None:
    """Pin invocation-only settings in existing session metadata after reserve.

    ``branch_id`` names a branch other than the selected one the call was
    made on: the called Agent and its model are written on that branch's
    own record, and the selected branch's live mirror is left alone."""
    from openprogram.runtime.context.session_db import default_db

    profile = invocation['profile_snapshot']
    model = invocation['resolved_model']
    provider, model_id = model.get('provider', ''), model.get('id', '')
    if branch_id and not invocation['agent_trial']:
        default_db().update_branch_config(session_id, branch_id, {
            "agent_id": invocation['agent_id'], "provider_override": provider,
            "model_override": model_id, "thinking_effort": profile.get('thinking_effort') or None,
        })
        return
    default_db().update_session(
        session_id,
        agent_id=invocation['agent_id'],
        agent_trial=invocation['agent_trial'],
        agent_profile_snapshot=deepcopy(profile) if invocation['agent_trial'] else None,
        provider_override=provider,
        model_override=model_id,
        thinking_effort=profile.get('thinking_effort') or None,
    )
    conv.update(agent_id=invocation['agent_id'], provider_override=provider,
                model_override=model_id, provider_name=provider)


def request_agent_invocation(session_id: str, invocation: dict | None) -> dict:
    """Build only runtime-owned fields; never forward arbitrary renderer keys."""
    from openprogram.runtime.conversation.session_config import load_agent_session_binding
    from openprogram.runtime.conversation.session_model import override_string, read_session_chat_model

    binding = load_agent_session_binding(session_id)
    if invocation is not None:
        binding['profile_snapshot'] = deepcopy(invocation['profile_snapshot'])
    if binding:
        provider, model_id = read_session_chat_model(session_id)
        binding['model_override'] = override_string(provider, model_id)
    return binding
