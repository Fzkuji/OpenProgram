"""Workflow receipts survive a repaired response without repeating tools."""
from openprogram.models.providers.types import AgentTool, AgentToolResult
from openprogram.runtime.agent.runtime import Runtime
from openprogram.models.providers.types import (
    AssistantMessage, EventDone, EventStart, EventTextDelta, TextContent, ToolCall,
)


def test_runtime_keeps_completed_tool_receipts_across_repetitive_response():
    calls = []
    executed = []

    async def execute(call_id, arguments, cancel=None, on_update=None):
        executed.append(call_id)
        return AgentToolResult(content=[TextContent(text='observed page')])

    tool = AgentTool(name='receipt_probe', label='Probe', description='Read only probe',
                     parameters={'type': 'object', 'properties': {}}, execute=execute)

    async def stream(model, context, options):
        calls.append(1)
        message = AssistantMessage(content=[], api=model.api, provider=model.provider,
                                   model=model.id, stop_reason='stop', timestamp=1)
        if len(calls) == 1:
            message.content = [ToolCall(id='read-once', name='receipt_probe', arguments={})]
            message.stop_reason = 'toolUse'
        elif len(calls) == 2:
            yield EventStart(partial=message)
            for _ in range(24):
                chunk = 'I will inspect the current browser page and check its visible fields before continuing. '
                message.content = [TextContent(text=(message.content[0].text if message.content else '') + chunk)]
                yield EventTextDelta(content_index=0, delta=chunk, partial=message)
            return
        else:
            message.content = [TextContent(text='done')]
        yield EventStart(partial=message)
        yield EventDone(reason=message.stop_reason, message=message)

    runtime = Runtime(call=lambda *_args, **_kwargs: 'unused')
    try:
        assert runtime.exec('inspect', tools=[tool], stream_fn=stream, max_iterations=None) == 'done'
        assert executed == ['read-once']
        receipts = [block for block in runtime.last_blocks if block.get('type') == 'tool']
        assert [block['tool_call_id'] for block in receipts] == ['read-once']
        assert receipts[0]['is_error'] is False
        assert 'observed page' in receipts[0]['result']
    finally:
        runtime.close()
