"""Persist actual report-origin dispatcher events through the host writer."""
from __future__ import annotations

from openprogram.runtime.agent.call_state import current_call_id
from openprogram.state.store import _store
import logging

_log = logging.getLogger(__name__)


class PrimeHistory:
    """Project real tool events without constructing a provider/LLM history."""

    def __init__(self, runtime):
        self.runtime, self.parent, self.store = runtime, current_call_id(), _store.get()
        self.nodes = {}
        if self.store is not None and self.parent not in self.store.load().nodes:
            raise ValueError('Weekly source prime has a writer but no actual calling node')

    async def consume(self, stream):
        async for event in stream:
            if event.type not in {'tool_execution_start', 'tool_execution_end'} or event.expose == 'hidden':
                continue
            identity = event.occurrence_id or event.tool_call_id
            start = event.type == 'tool_execution_start'
            args = {'parent_node_id': self.parent, 'tool_call_id': event.tool_call_id,
                    'occurrence_id': identity}
            node_id = self.nodes.get(identity, '')
            if start:
                node_id = self.runtime._ensure_nested_tool_node(**args, tool_name=event.tool_name,
                    arguments=event.args, expose=event.expose)
                self.nodes[identity] = node_id
                if self.store is not None:
                    if not node_id or node_id not in self.store.load().nodes:
                        raise ValueError('Weekly source prime could not persist its actual tool start')
                    self.store.update(node_id, metadata={'source': 'weekly_source_prime',
                        'origin': 'weekly_source_prime', 'invocation_origin': 'program'})
            else:
                text = '\n'.join(c.text for c in event.result.content if getattr(c, 'type', '') == 'text')
                self.runtime._finish_nested_tool_node(**args, node_id=node_id, result=text,
                    is_error=event.is_error, outcome=event.outcome)
                if self.store is not None:
                    node = self.store.load().nodes.get(node_id)
                    status = ('cancelled' if event.outcome == 'cancelled' else
                              'error' if event.is_error or event.outcome == 'failed' else
                              'pending' if event.outcome == 'not_started' else 'completed')
                    if node is not None and node.metadata.get('status') != status:
                        # A guarded tool's wrapper records "completed" when it
                        # returns its refusal as a value; the dispatcher's
                        # outcome is the actual one.
                        self.store.update(node_id, metadata={'status': status, 'is_error': bool(event.is_error),
                            'outcome': event.outcome or status})
                        node = self.store.load().nodes.get(node_id)
                    if node is None or node.metadata.get('status') != status:
                        raise ValueError('Weekly source prime could not persist its actual tool outcome')
                    self.store.update(node_id, metadata={'result_json': event.result.model_dump(mode='json')})
            if self.store is not None:
                node = self.store.load().nodes.get(node_id)
                if (node is None or node.metadata.get('source') != 'weekly_source_prime'
                        or node.metadata.get('origin') != 'weekly_source_prime'
                        or node.metadata.get('invocation_origin') != 'program'
                        or not start and node.metadata.get('result_json') != event.result.model_dump(mode='json')):
                    raise ValueError('Weekly source prime lost its program-origin record')
            callback = self.runtime.on_stream
            if callback:
                payload = {'type': 'tool_use' if start else 'tool_result', 'origin': 'weekly_source_prime',
                    'tool_call_id': event.tool_call_id, 'occurrence_id': identity, 'tool': event.tool_name,
                    'node_id': node_id, 'ref_node_id': node_id, 'expose': event.expose}
                payload.update({'input': str(event.args)} if start else
                    {'result': text, 'is_error': event.is_error, 'outcome': event.outcome})
                try:
                    callback(payload)
                except Exception:
                    # A display callback cannot erase the already persisted actual receipt.
                    _log.debug("weekly source prime stream callback failed for %s", event.tool_call_id, exc_info=True)

    def receipt(self, receipt):
        if self.store is None:
            return
        matches = [node_id for node_id in self.nodes.values()
                   if self.store.load().nodes[node_id].metadata.get('tool_call_id') == receipt.tool_call_id]
        if len(matches) != 1:
            raise ValueError('Weekly source prime receipt lacks its exact durable tool node')
        value = receipt.model_dump(mode='json')
        self.store.update(matches[0], metadata={'receipt': value})
        if self.store.load().nodes[matches[0]].metadata.get('receipt') != value:
            raise ValueError('Weekly source prime could not persist its actual returned receipt')
