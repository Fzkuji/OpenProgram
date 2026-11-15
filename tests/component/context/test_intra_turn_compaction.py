"""One user message whose tool rounds outgrow the window is checkpointed
before the next call; tool results are never summarised one by one."""

import asyncio

import pytest

from openprogram.runtime.agent.loop import agent_loop
from openprogram.runtime.agent.loop.types import AgentContext, AgentLoopConfig
from openprogram.models.providers.types import AgentTool, AgentToolResult
from openprogram.runtime.context.tokens import estimate_history_tokens
from openprogram.models.providers.types import (
    AssistantMessage,
    EventDone,
    Model,
    TextContent,
    ToolCall,
    UserMessage,
)
from tests.support.summary_stream import install_summary_reply


@pytest.mark.parametrize("requested,expected", [(None, 739), (100, 100), (900, None)])
def test_small_window_default_matches_provider_output_limit(requested, expected):
    model = Model(
        id="small",
        name="small",
        api="openai-completions",
        provider="openai",
        base_url="https://example.invalid",
        context_window=1000,
        max_tokens=8192,
    )
    seen = []

    async def stream(_model, context, options):
        seen.append(options.max_tokens)
        result = AssistantMessage(
            content=[TextContent(text="done")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            timestamp=0,
        )
        yield EventDone(reason="stop", message=result)

    async def run():
        events = agent_loop(
            [UserMessage(content="hello", timestamp=0)],
            AgentContext(tools=[], memory_prefetch=""),
            AgentLoopConfig(
                model=model,
                max_tokens=requested,
                convert_to_llm=lambda messages: messages,
            ),
            stream_fn=stream,
        )
        async for _ in events:
            pass
        return await events.result()

    if expected is None:
        with pytest.raises(ValueError, match="Protected request"):
            asyncio.run(run())
        assert not seen
    else:
        asyncio.run(run())
        assert seen == [expected]


def test_one_user_message_multiple_tool_rounds(monkeypatch):
    model = Model(
        id="fixture",
        name="fixture",
        api="openai-completions",
        provider="openai",
        base_url="https://example.invalid",
        context_window=12000,
        max_tokens=1000,
    )
    payload = "\n".join(
        f"item {i}: check failed at position {i * 17}" for i in range(3500)
    )
    requests = []
    summaries = []
    # A result is bounded to WINDOW_SHARE of the window when it is made, so
    # no single round fills it: the growth over many rounds of one message
    # is what has to be compacted.
    rounds = 12

    def answer(content, stop="stop"):
        return AssistantMessage(
            content=content,
            api=model.api,
            provider=model.provider,
            model=model.id,
            stop_reason=stop,
            timestamp=0,
        )

    async def summary(_model, context, options):
        summaries.append(context)
        return answer(
            [
                TextContent(
                    text="Checks failed; inspect reported positions before making changes."
                )
            ]
        )

    install_summary_reply(monkeypatch, summary)

    async def execute(*_args):
        return AgentToolResult(content=[TextContent(text=payload)])

    async def stream(_model, context, options=None):
        requests.append(context.model_copy(deep=True))
        i = len(requests)
        result = (
            # Each round checks another part, so it is not the same call repeated.
            answer([ToolCall(id=f"c{i}", name="check", arguments={"part": i})], "toolUse")
            if i <= rounds
            else answer([TextContent(text="analysis done")])
        )
        yield EventDone(reason=result.stop_reason, message=result)

    async def run():
        events = agent_loop(
            [UserMessage(content="Only analyze. Ask before editing.", timestamp=0)],
            AgentContext(
                tools=[
                    AgentTool(
                        name="check",
                        label="check",
                        description="check",
                        parameters={"type": "object"},
                        execute=execute,
                    )
                ],
                memory_prefetch="",
            ),
            AgentLoopConfig(
                model=model, convert_to_llm=lambda messages: messages, max_tokens=1000
            ),
            stream_fn=stream,
        )
        async for _ in events:
            pass
        return await events.result()

    raw = asyncio.run(run())
    assert summaries, "same-message tool growth must trigger request compaction"
    assert len(requests) == rounds + 1
    assert all(estimate_history_tokens(r.messages) < 11000 for r in requests)
    assert all(
        r.messages[0].content == "Only analyze. Ask before editing." for r in requests
    )
    # The turn keeps each result as the model read it: the payload bounded to
    # its share of the window (measured by weight, not characters), never
    # the summary that replaced it in a later request.
    from openprogram.state.store.session.spill import WINDOW_SHARE
    from openprogram.state.store.session.weight import weight

    kept = [m.content[0].text for m in raw if m.role == "toolResult"]
    assert len(kept) == rounds
    assert all(k.startswith(payload[:1000]) and len(k) < len(payload)
               and weight(k) <= 12000 * WINDOW_SHARE for k in kept)
    for request in requests:
        calls = {
            b.id
            for m in request.messages
            if m.role == "assistant"
            for b in m.content
            if isinstance(b, ToolCall)
        }
        assert {
            m.tool_call_id for m in request.messages if m.role == "toolResult"
        } == calls


def test_native_schema_is_part_of_fixed_input_budget():
    import pytest
    from openprogram.runtime.context.compaction.request import RequestCompactor
    from openprogram.models.providers.types import (
        Context,
        SimpleStreamOptions,
        JsonSchemaOutput,
    )

    model = Model(
        id="fixture",
        name="fixture",
        api="openai-completions",
        provider="openai",
        base_url="https://example.invalid",
        context_window=6000,
        max_tokens=512,
    )
    context = Context(messages=[UserMessage(content="Analyze.", timestamp=0)])
    schema = {
        "type": "object",
        "description": " ".join(f"evidence_{i}" for i in range(6000)),
    }
    options = SimpleStreamOptions(
        max_tokens=512, response_format=JsonSchemaOutput(schema=schema)
    )
    with pytest.raises(ValueError, match="Protected request"):
        asyncio.run(RequestCompactor().prepare(context, model, options))


@pytest.mark.parametrize("mode", ["raised", "aborted", "signal"])
def test_cancelled_summary_never_changes_raw_messages(monkeypatch, mode):
    import pytest
    from openprogram.runtime.context.compaction.request import RequestCompactor
    from openprogram.models.providers.types import (
        Context,
        SimpleStreamOptions,
        ToolResultMessage,
    )

    model = Model(
        id="fixture",
        name="fixture",
        api="openai-completions",
        provider="openai",
        base_url="https://example.invalid",
        context_window=6000,
        max_tokens=512,
    )
    call = AssistantMessage(
        content=[ToolCall(id="x", name="check", arguments={})],
        api=model.api,
        provider=model.provider,
        model=model.id,
        timestamp=0,
    )
    result = ToolResultMessage(
        tool_call_id="x",
        tool_name="check",
        content=[TextContent(text="evidence 1234567 " * 800)],
        timestamp=0,
    )
    # A second round makes the first a complete prefix the checkpoint reads.
    later = call.model_copy(update={"content": [ToolCall(id="y", name="check", arguments={})]})
    context = Context(
        messages=[
            UserMessage(content="Only analyze. " * 450, timestamp=0),
            call,
            result,
            later,
            result.model_copy(update={"tool_call_id": "y", "content": [TextContent(text="ok")]}),
        ]
    )
    before = context.model_dump_json()
    import threading

    signal = threading.Event()
    compactor = RequestCompactor()
    calls = []

    async def summary(*_args):
        calls.append(True)
        if mode == "raised":
            raise asyncio.CancelledError()
        if mode == "signal":
            signal.set()
        return AssistantMessage(
            content=[TextContent(text="incomplete evidence")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            timestamp=0,
            stop_reason="aborted" if mode == "aborted" else "stop",
        )

    install_summary_reply(monkeypatch, summary)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            compactor.prepare(
                context, model, SimpleStreamOptions(max_tokens=512, signal=signal)
            )
        )
    assert context.model_dump_json() == before
    assert compactor._checkpoint is None
    assert len(calls) == 1


@pytest.mark.parametrize("requested,expected", [(None, [8192, 739]), (100, [100, 100])])
def test_fallback_resolves_its_own_default_output_cap(monkeypatch, requested, expected):
    from types import SimpleNamespace
    import importlib

    primary = Model(
        id="primary",
        name="fixture",
        api="openai-completions",
        provider="openai",
        base_url="https://example.invalid",
        context_window=12000,
        max_tokens=8192,
    )
    fallback = primary.model_copy(update={"id": "fallback", "context_window": 1000})
    dispatched = []

    async def provider_stream(provider, model, context, options, **kwargs):
        dispatched.append(options.max_tokens)
        if model.id == "primary":
            raise ConnectionError("primary unavailable")
        yield EventDone(
            reason="stop",
            message=AssistantMessage(
                content=[TextContent(text="ok")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                timestamp=0,
            ),
        )

    monkeypatch.setattr(
        "openprogram.models.providers.catalog.api_registry.resolve_api_provider_snapshot",
        lambda model: SimpleNamespace(
            provider=object(), supports_idempotency_key=False
        ),
    )
    monkeypatch.setattr(
        "openprogram.models.providers.utils.failover.resolve_fallback_models",
        lambda model: [fallback],
    )
    monkeypatch.setattr(
        importlib.import_module("openprogram.models.providers.streaming.stream"),
        "stream_simple_with_provider",
        provider_stream,
    )

    async def run():
        events = agent_loop(
            [UserMessage(content="hello", timestamp=0)],
            AgentContext(tools=[], memory_prefetch=""),
            AgentLoopConfig(
                model=primary,
                convert_to_llm=lambda messages: messages,
                max_tokens=requested,
            ),
        )
        await events.result()

    asyncio.run(run())
    assert dispatched == expected


def test_fallback_rechecks_actual_model_window(monkeypatch):
    from types import SimpleNamespace
    import pytest

    primary = Model(
        id="primary",
        name="fixture",
        api="openai-completions",
        provider="openai",
        base_url="https://example.invalid",
        context_window=12000,
        max_tokens=512,
    )
    fallback = primary.model_copy(update={"id": "fallback", "context_window": 6000})
    dispatched = []

    async def provider_stream(provider, model, context, options, **kwargs):
        dispatched.append(model.id)
        if model.id == "primary":
            raise ConnectionError("primary unavailable")
        yield EventDone(
            reason="stop",
            message=AssistantMessage(
                content=[TextContent(text="ok")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                timestamp=0,
            ),
        )

    monkeypatch.setattr(
        "openprogram.models.providers.catalog.api_registry.resolve_api_provider_snapshot",
        lambda model: SimpleNamespace(
            provider=object(), supports_idempotency_key=False
        ),
    )
    monkeypatch.setattr(
        "openprogram.models.providers.utils.failover.resolve_fallback_models",
        lambda model: [fallback],
    )
    import importlib

    monkeypatch.setattr(
        importlib.import_module("openprogram.models.providers.streaming.stream"),
        "stream_simple_with_provider",
        provider_stream,
    )

    async def run():
        events = agent_loop(
            [
                UserMessage(
                    content=" ".join(f"evidence_{i}" for i in range(2000)), timestamp=0
                )
            ],
            AgentContext(tools=[], memory_prefetch=""),
            AgentLoopConfig(
                model=primary, convert_to_llm=lambda messages: messages, max_tokens=512
            ),
        )
        with pytest.raises(ValueError, match="Protected request"):
            await events.result()

    asyncio.run(run())
    assert dispatched == ["primary"]


def test_default_output_uses_model_capacity_without_compacting_short_input():
    from openprogram.runtime.context.compaction.request import RequestCompactor
    from openprogram.models.providers.types import Context, SimpleStreamOptions

    model = Model(
        id="large",
        name="large",
        api="openai-completions",
        provider="openai",
        base_url="https://example.invalid",
        context_window=200000,
        max_tokens=64000,
    )
    context = Context(messages=[UserMessage(content="hello", timestamp=0)])
    options = SimpleStreamOptions()
    result = asyncio.run(RequestCompactor().prepare(context, model, options))
    assert options.max_tokens == 64000
    assert result.messages == context.messages


@pytest.mark.parametrize("requested", [None, 2000])
def test_bedrock_thinking_cannot_expand_resolved_total_limit(monkeypatch, requested):
    from openprogram.runtime.context.compaction.request import RequestCompactor
    from openprogram.models.providers.types import Context, SimpleStreamOptions
    from openprogram.models.providers.adapters.amazon_bedrock import amazon_bedrock

    model = Model(
        id="anthropic.claude-3-7-sonnet",
        name="Claude",
        api="bedrock-converse-stream",
        provider="amazon-bedrock",
        base_url="https://example.invalid",
        context_window=10000,
        max_tokens=20000,
    )
    context = Context(messages=[UserMessage(content="hello", timestamp=0)])
    options = SimpleStreamOptions(max_tokens=requested, reasoning="medium")
    result = asyncio.run(RequestCompactor().prepare(context, model, options))
    captured = []
    monkeypatch.setattr(
        amazon_bedrock, "stream_bedrock", lambda m, c, opts: captured.append(opts)
    )
    if requested:
        with pytest.raises(ValueError, match="thinking budget"):
            amazon_bedrock.stream_simple_bedrock(model, result, options)
        assert not captured
    else:
        amazon_bedrock.stream_simple_bedrock(model, result, options)
        assert captured[0]["max_tokens"] == options.max_tokens < model.context_window


def test_context_bind_publishes_writer_for_direct_runtime_and_restores(tmp_path):
    from openprogram.runtime.agent import Runtime
    from openprogram.runtime.context import Context
    from openprogram.state.store.session.nodes import Call, ROLE_USER
    from openprogram.state.store import SessionStore, SessionNodeWriter, _store

    store = SessionStore(tmp_path / "sessions")
    store.create_session("bound", agent_id="main")
    store.create_session("other", agent_id="main")
    writer = SessionNodeWriter(store, "bound")
    other = SessionNodeWriter(store, "other")
    branch = Call(role=ROLE_USER, output="selected-branch", predecessor="ROOT")
    sibling = Call(role=ROLE_USER, output="unselected-branch", predecessor="ROOT")
    writer.append(branch)
    writer.append(sibling)
    seen = []
    runtime = Runtime(
        call=lambda content, **kwargs: seen.append(content) or "direct-result"
    )
    previous_writer = _store.get()
    previous_context = Context.current()
    try:
        with Context(store=writer, head_id=branch.id).bind():
            assert _store.get() is writer
            assert runtime.exec("direct-input", toolset="none") == "direct-result"
            with pytest.raises(ValueError, match="differs from the active"):
                with Context(store=other).bind():
                    pass
            assert _store.get() is writer
        assert _store.get() is previous_writer
        assert Context.current() is previous_context
        with Context(store=writer).bind():
            runtime.exec("continued-input", toolset="none")
        text = "\n".join(block.get("text", "") for block in seen[0])
        assert "selected-branch" in text and "unselected-branch" not in text
        assert any("direct-input" in block.get("text", "") for block in seen[1])
        assert len(writer.load().nodes) == 4
    finally:
        runtime.close()
        store.close()


def test_whole_prefix_request_summary_keeps_the_summary_rules(monkeypatch):
    from openprogram.models.providers.types import Context, SimpleStreamOptions, ToolResultMessage
    from openprogram.runtime.context.compaction import bounded_summary, prompts
    from openprogram.runtime.context.compaction.request import RequestCompactor

    model = Model(id="fixture", name="fixture", api="openai-completions", provider="openai",
                  base_url="https://example.invalid", context_window=6000, max_tokens=512)
    messages = [UserMessage(content="Only analyze.", timestamp=0)]
    for index in range(12):
        tool_id = f"x{index}"
        messages.append(AssistantMessage(content=[ToolCall(id=tool_id, name="check", arguments={"text": "large input " * 300})],
                                         api=model.api, provider=model.provider, model=model.id, timestamp=0))
        messages.append(ToolResultMessage(tool_call_id=tool_id, tool_name="check",
                                          content=[TextContent(text="evidence")], timestamp=0))
    seen = []

    async def capture(source, used_model, options, *, instruction, max_tokens, remaining=None):
        seen.append(instruction)
        return "short"

    monkeypatch.setattr(bounded_summary, "summarize_text", capture)
    asyncio.run(RequestCompactor().prepare(Context(messages=messages), model, SimpleStreamOptions()))
    assert any(prompts.SUMMARY_RULES in instruction for instruction in seen)


def test_whole_prefix_request_summary_reads_no_opaque_payloads(monkeypatch):
    """A finished turn renders with its reasoning items (to the model that
    wrote them), and its prefix is still summarized: the summarizer reads
    what the turn said and did and the reasoning summary, but not a
    reasoning item's encrypted content, a message id or a thought
    signature, which mean nothing to it. The turn in progress keeps its
    signed reasoning (test_checkpoint_rejects_protected_prefix)."""
    import json as _json

    from openprogram.models.providers.types import (
        Context, SimpleStreamOptions, ThinkingContent, ToolResultMessage)
    from openprogram.runtime.context.compaction import bounded_summary
    from openprogram.runtime.context.compaction.request import RequestCompactor

    model = Model(id="fixture", name="fixture", api="openai-codex", provider="openai-codex",
                  base_url="https://example.invalid", context_window=6000, max_tokens=512)
    messages = [UserMessage(content="Only analyze.", timestamp=0)]
    for index in range(12):
        tool_id = f"x{index}"
        item = {"type": "reasoning", "summary": [{"type": "summary_text", "text": f"plan {index}"}],
                "encrypted_content": "gAAAA" + "Z" * 2000}
        messages.append(AssistantMessage(content=[
            ThinkingContent(thinking=f"plan {index}", thinking_signature=_json.dumps(item)),
            ThinkingContent(thinking="", thinking_signature="opaque-redacted", redacted=True),
            TextContent(text=f"checking {index}", text_signature=f"msg_secret_{index}"),
            ToolCall(id=tool_id, name="check", arguments={"text": "large input " * 300},
                     thought_signature="sig-secret"),
        ], api=model.api, provider=model.provider, model=model.id, timestamp=0))
        messages.append(ToolResultMessage(tool_call_id=tool_id, tool_name="check",
                                          content=[TextContent(text="evidence")], timestamp=0))
    messages.append(UserMessage(content="Go on.", timestamp=0))
    messages.append(AssistantMessage(content=[TextContent(text="Current step.")],
                                     api=model.api, provider=model.provider, model=model.id, timestamp=0))
    sources = []

    async def capture(source, used_model, options, *, instruction, max_tokens, remaining=None):
        sources.append(source)
        return "short"

    monkeypatch.setattr(bounded_summary, "summarize_text", capture)
    prepared = asyncio.run(RequestCompactor().prepare(Context(messages=messages), model, SimpleStreamOptions()))
    assert any(m.role == "assistant" and "[Earlier execution summary]" in m.content[0].text
               for m in prepared.messages)
    assert [m.content for m in prepared.messages if m.role == "user"] == ["Only analyze.", "Go on."]
    assert prepared.messages[-1].content == messages[-1].content
    whole = [source for source in sources if "Only analyze." in source]
    assert whole, "the whole-prefix summary ran"
    for opaque in ("gAAAA", "msg_secret_", "sig-secret", "opaque-redacted"):
        assert opaque not in whole[0], opaque
    assert "plan 0" in whole[0] and "checking 0" in whole[0] and "large input" in whole[0]
