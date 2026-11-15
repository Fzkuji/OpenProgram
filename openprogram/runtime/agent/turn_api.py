"""Session execution shared by configured Agents and conversation subtypes.

A persistent turn runs on the conversation engine through ``engine_link``.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar, copy_context
from copy import copy, deepcopy
import threading

from openprogram.runtime.agent import engine_link
from openprogram.runtime.agent.turn_request import _InheritParent
from openprogram.runtime.context import Context
import logging

_log = logging.getLogger(__name__)

_current_turn_agent = ContextVar('current_turn_agent', default=None)


class TurnOperations:
    """Persistent turns reuse the existing durable runtime and event protocol."""

    def _turn_request(self, request):
        """Apply instance configuration before the runtime freezes its contract."""
        spec = getattr(self, '_spec', None)
        if spec is None and not self._options and all(getattr(self, name, None) is None for name in
                                ('model', 'instructions', 'tools', 'effort')):
            return request
        request = copy(request)
        profile = deepcopy(request.profile_snapshot if request.profile_snapshot is not None
                           else spec.to_dict() if spec is not None
                           else engine_link.agent_profile(request.agent_id))
        if self.instructions is not None:
            profile['system_prompt'] = self.instructions
        if self.model is not None:
            model = self.model
            if spec is not None and model == spec.model.id and spec.model.provider:
                model = f'{spec.model.provider}/{model}'
            request.model_override = model
        if self.effort is not None:
            request.thinking_effort = self.effort
        if self.tools is not None:
            if any(not isinstance(tool, str) for tool in self.tools):
                raise TypeError('Persistent turn tools must be registered tool names.')
            request.tools_override = [tool for tool in self.tools
                                      if request.tools_override is None or tool in request.tools_override]
        if self._options.get('response_format') is not None:
            request.response_format = self._options['response_format']
        policy = dict(profile.get('tools') or {}) if isinstance(profile.get('tools'), dict) else {}
        if self._options.get('tools_deny'):
            policy['disabled'] = list(dict.fromkeys([*(policy.get('disabled') or []),
                                                    *self._options['tools_deny']]))
            profile['tools'] = policy
        turn_options = {name: self._options[name] for name in
                        ('max_iterations', 'tool_choice', 'parallel_tool_calls')
                        if name in self._options}
        if turn_options:
            profile['_agent_turn_options'] = turn_options
        request.profile_snapshot = profile
        return request

    @contextmanager
    def _turn_scope(self, request, context=None):
        from .runtime_scope import _agent_owner, _agent_graph_owner
        ambient = Context.current()
        active = ambient.derive() if ambient is not None else Context()
        if self.context is not None:
            active = active.merge(self.context)
        if context is not None:
            if not isinstance(context, Context):
                raise TypeError('Agent context must be a Context.')
            active = active.merge(context)
        if active.session_id is not None and active.session_id != request.session_id:
            raise ValueError('Agent request and Context must identify the same session.')
        if active.head_id is not None:
            if isinstance(request.branch_from, _InheritParent):
                request.branch_from = active.head_id
        with ExitStack() as stack:
            stack.enter_context(active.bind())
            for variable in (_current_turn_agent, _agent_owner, _agent_graph_owner):
                token = variable.set(self)
                stack.callback(variable.reset, token)
            yield

    def run_turn(self, request, *, context=None, on_event=None, cancel_event=None,
                 execution_context=None):
        """Run a persistent session turn, emitting the standard runtime events.

        The request retains transport authority and durable execution identity.
        A caller may supply Context.for_session for an explicit session store.
        Pause/steer ownership remains with the existing production driver.
        """
        request = self._turn_request(request)
        with self._turn_scope(request, context):
            return copy_context().run(engine_link.run_turn, request, on_event=on_event,
                                      cancel_event=cancel_event,
                                      execution_context=execution_context)

    def resume_turn(self, continuation, *, context=None, on_event=None,
                    cancel_event=None, execution_context=None):
        """Resume the saved request and configuration, without re-admission."""
        with self._turn_scope(continuation.request, context):
            return copy_context().run(engine_link.resume_turn, continuation, on_event=on_event,
                                        cancel_event=cancel_event,
                                        execution_context=execution_context)

    async def _async_turn(self, operation, value, **options):
        cancel = options.get('cancel_event')
        if cancel is None:
            cancel = threading.Event()
            options['cancel_event'] = cancel
        worker = asyncio.create_task(asyncio.to_thread(operation, value, **options))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Fired, not only set: the turn's loop and the tool it runs are
            # cut at once (``run_control.wakeups``), not at the next poll.
            from openprogram.runtime.execution.run_control.wakeups import set_and_fire
            set_and_fire(cancel)
            # Do not leave the owner thread writing after this call unwinds.
            # A caller under an anyio cancel scope is cancelled again at
            # every await, so one shielded await would return at once.
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not worker.cancelled() and worker.exception() is not None:
                # The cancellation is what propagates; the worker's own error is moot.
                _log.debug("worker failed after cancellation", exc_info=worker.exception())
            raise

    async def arun_turn(self, request, **options):
        """Await a session turn; cancelling the task cancels its running turn."""
        return await self._async_turn(self.run_turn, request, **options)

    async def aresume_turn(self, continuation, **options):
        """Await a saved continuation with the same cancellation contract."""
        return await self._async_turn(self.resume_turn, continuation, **options)
