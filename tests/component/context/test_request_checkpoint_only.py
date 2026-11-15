"""A request is never rewritten result by result (context/compaction.html,
"Request checkpoint").

- Under the window, a turn's requests only grow at the end: each request's
  bytes start with the previous one's, however large the tool results.
- Over the window, a call that cannot compact durably gets one checkpoint
  summary, and no tool result is summarised on its own.
"""
import asyncio

from openprogram.models.providers.types import (
    AssistantMessage, Context, Model, SimpleStreamOptions, TextContent, ToolCall,
    ToolResultMessage, UserMessage,
)
from openprogram.runtime.context.compaction.request import RequestCompactor
from tests.support.summary_stream import install_summary_reply


def _model(window):
    return Model(id='checkpoint', name='fixture', api='openai-completions', provider='openai',
                 base_url='https://example.invalid', context_window=window, max_tokens=512)


def _step(i, size):
    call = AssistantMessage(content=[ToolCall(id=f'c{i}', name='inspect', arguments={})],
                            api='openai-completions', provider='openai', model='checkpoint', timestamp=0)
    result = ToolResultMessage(tool_call_id=f'c{i}', tool_name='inspect',
                               content=[TextContent(text=f'evidence {i} ' * size)], timestamp=0)
    return [call, result]


def test_under_the_window_requests_only_grow_at_the_end(monkeypatch):
    calls = []

    async def reply(*_a):
        calls.append(1)
        raise AssertionError('no summary under the window')

    install_summary_reply(monkeypatch, reply)
    compactor = RequestCompactor()
    messages = [UserMessage(content='inspect everything', timestamp=0)]
    previous = None

    async def run():
        nonlocal previous, messages
        for i in range(6):
            messages = [*messages, *_step(i, 3000)]
            sent = await compactor.prepare(Context(messages=messages), _model(400_000),
                                           SimpleStreamOptions(max_tokens=512))
            dumped = [m.model_dump_json() for m in sent.messages]
            if previous is not None:
                assert dumped[:len(previous)] == previous
            previous = dumped
    asyncio.run(run())
    assert calls == []


def test_over_the_window_one_checkpoint_and_no_per_result_summary(monkeypatch):
    asked = []

    async def reply(model, context, _options):
        asked.append(context.system_prompt)
        return AssistantMessage(content=[TextContent(text='Earlier steps inspected the repo.')],
                                api=model.api, provider=model.provider, model=model.id, timestamp=0)

    install_summary_reply(monkeypatch, reply)
    messages = [UserMessage(content='inspect everything', timestamp=0)]
    for i in range(6):
        messages += _step(i, 300)
    messages.append(AssistantMessage(content=[TextContent(text='next')], api='openai-completions',
                                     provider='openai', model='checkpoint', timestamp=0))
    sent = asyncio.run(RequestCompactor().prepare(Context(messages=messages), _model(6000),
                                                  SimpleStreamOptions(max_tokens=512)))
    # One checkpoint, read in as many bounded parts as its source needs.
    assert asked and all('Summarize this conversation' in a for a in asked)
    assert not any(isinstance(m, ToolResultMessage) and 'Tool evidence summary' in m.content[0].text
                   for m in sent.messages)
    assert sent.messages[0].content == 'inspect everything'
