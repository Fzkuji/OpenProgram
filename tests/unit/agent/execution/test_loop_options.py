"""exec-level loop options (tool_choice / parallel_tool_calls /
max_iterations) reach the agent loop and the provider call.

These three were accepted-but-inert from the AgentSession unification
(dd4c87c6) until the 2026-06 re-wiring; the tests pin the whole chain —
exec kwargs → _current_loop_opts → AgentSession → AgentLoopConfig →
stream options / loop cap — so it cannot silently break again.
"""

from __future__ import annotations

import asyncio
import time

from openprogram.runtime.agent.loop.session import AgentSession
from openprogram.models.providers.types import AgentToolResult
from openprogram.models.providers.types import (
    AssistantMessage,
    Model,
    EventDone,
    EventStart,
    TextContent,
    ToolCall,
    ToolResultMessage,
)
from openprogram.programs._host.runtime import (
    ToolReturn,
    function,
    restore_registry,
    snapshot_registry,
)
from openprogram.models.providers.structured_output import normalize_response_format
from openprogram.runtime.agent.runtime import (
    Runtime,
    _current_loop_opts,
)


def _assistant(content) -> AssistantMessage:
    has_calls = any(isinstance(c, ToolCall) for c in content)
    return AssistantMessage(
        content=content,
        api="openai-completions",
        provider="openai",
        model="fake",
        stop_reason="toolUse" if has_calls else "stop",
        timestamp=int(time.time() * 1000),
    )


def _tool_call_msg(i: int = 0) -> AssistantMessage:
    return _assistant([ToolCall(id=f"call_{i}", name="echo", arguments={})])


def _text_msg() -> AssistantMessage:
    return _assistant([TextContent(text="done")])


def _make_stream_fn(replies: list[AssistantMessage]):
    """Fake provider: returns replies in order (last one repeats),
    records how many model calls happened and the opts of each."""
    state = {"calls": 0, "opts": []}

    def stream_fn(model, context, opts):
        state["opts"].append(opts)
        idx = min(state["calls"], len(replies) - 1)
        state["calls"] += 1
        msg = replies[idx]

        async def gen():
            yield EventStart(partial=msg)
            yield EventDone(
                reason="toolUse" if msg.stop_reason == "toolUse" else "stop",
                message=msg,
            )

        return gen()

    return stream_fn, state


async def _echo_execute(call_id, args, cancel, on_update):
    return AgentToolResult(content=[TextContent(text="echoed")])


def _echo_tool():
    from openprogram.models.providers.types import AgentTool

    return AgentTool(
        name="echo",
        description="Echo test tool.",
        parameters={"type": "object", "properties": {}},
        label="echo",
        execute=_echo_execute,
    )


def _fake_model() -> Model:
    # Constructed directly: the registry is config-dependent (enabled
    # models), so unit tests must not resolve models through it.
    return Model(id="fake", name="fake", api="openai-completions",
                 provider="openai", base_url="https://example.invalid/v1")


def _session(stream_fn, *, model=None, **kwargs) -> AgentSession:
    session = AgentSession(
        model=model or _fake_model(),
        tools=[_echo_tool()],
        **kwargs,
    )
    session._agent.stream_fn = stream_fn
    return session


def test_max_iterations_caps_the_loop():
    # The model always asks for one more tool call; the cap must stop it.
    stream_fn, state = _make_stream_fn([_tool_call_msg()])
    session = _session(stream_fn, max_iterations=2)
    asyncio.run(session.run("go"))
    assert state["calls"] == 2


def test_default_cap_does_not_kick_in_early():
    # Three tool rounds then a text answer — with no caller cap the
    # loop must run all four model calls.
    stream_fn, state = _make_stream_fn(
        [_tool_call_msg(0), _tool_call_msg(1), _tool_call_msg(2), _text_msg()]
    )
    session = _session(stream_fn)
    asyncio.run(session.run("go"))
    assert state["calls"] == 4


def test_chat_turns_have_no_hard_cap():
    from openprogram.runtime.agent.loop import MAX_INNER_ITERATIONS, iteration_cap_for

    assert MAX_INNER_ITERATIONS is None
    assert iteration_cap_for(None) is None
    assert iteration_cap_for(20) == 20
    assert iteration_cap_for(500) == 500
    assert iteration_cap_for(0) == 1


def test_tool_choice_and_parallel_reach_stream_opts():
    stream_fn, state = _make_stream_fn([_text_msg()])
    session = _session(
        stream_fn, tool_choice="required", parallel_tool_calls=False
    )
    asyncio.run(session.run("go"))
    opts = state["opts"][0]
    assert opts.tool_choice == "required"
    assert opts.parallel_tool_calls is False


def test_defaults_leave_stream_opts_unset():
    stream_fn, state = _make_stream_fn([_text_msg()])
    session = _session(stream_fn)
    asyncio.run(session.run("go"))
    opts = state["opts"][0]
    assert opts.tool_choice is None
    assert opts.parallel_tool_calls is None


def test_unsupported_model_drops_stale_thinking_effort():
    stream_fn, state = _make_stream_fn([_text_msg()])
    session = _session(stream_fn, thinking_level="high")

    asyncio.run(session.run("go"))

    assert state["opts"][0].reasoning is None


def test_supported_model_keeps_thinking_effort():
    stream_fn, state = _make_stream_fn([_text_msg()])
    model = _fake_model().model_copy(update={
        "reasoning": True,
        "thinking_levels": ["low", "high"],
    })
    session = _session(stream_fn, model=model, thinking_level="high")

    asyncio.run(session.run("go"))

    assert state["opts"][0].reasoning == "high"


def test_runtime_typed_error_reaches_tool_result_message() -> None:
    saved = snapshot_registry()
    try:
        @function(name="typed_failure_chain")
        def typed_failure_chain() -> ToolReturn:
            return ToolReturn(text="failed", is_error=True)

        stream_fn, _state = _make_stream_fn([
            _assistant([
                ToolCall(
                    id="typed-call",
                    name="typed_failure_chain",
                    arguments={},
                )
            ]),
            _text_msg(),
        ])
        session = AgentSession(
            model=_fake_model(),
            tools=[typed_failure_chain],
        )
        session._agent.stream_fn = stream_fn

        asyncio.run(session.run("go"))

        messages = [
            message
            for message in session._agent.state.messages
            if isinstance(message, ToolResultMessage)
        ]
        assert len(messages) == 1
        assert messages[0].is_error is True
        assert messages[0].details is None or (
            "is_error" not in messages[0].details
        )
    finally:
        restore_registry(saved)


def test_response_format_reaches_stream_opts():
    stream_fn, state = _make_stream_fn([
        _assistant([TextContent(text='{"answer":"done"}')])
    ])
    response_format = normalize_response_format({
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    })
    session = _session(
        stream_fn,
        model=_fake_model().model_copy(update={
            "base_url": "https://api.openai.com/v1",
        }),
        response_format=response_format,
    )

    asyncio.run(session.run("go"))

    assert state["opts"][0].response_format == response_format


# exec → _current_loop_opts normalisation


class _ProbeRuntime(Runtime):
    """Capture what exec() published to _current_loop_opts at call time."""

    def __init__(self, captured: dict):
        super().__init__(call=lambda *a, **kw: "ok", model="dummy")
        self._captured = captured

    def _call(self, content, model="default", response_format=None):
        self._captured["opts"] = _current_loop_opts.get(None)
        return "ok"


def test_exec_publishes_only_non_default_loop_opts():
    captured: dict = {}
    rt = _ProbeRuntime(captured)
    rt.exec(
        [{"type": "text", "text": "hi"}],
        tool_choice="required",
        parallel_tool_calls=False,
        max_iterations=5,
    )
    assert captured["opts"] == {
        "tool_choice": "required",
        "parallel_tool_calls": False,
        "max_iterations": 5,
    }


def test_exec_defaults_publish_only_the_20_round_cap():
    # "auto" / True are provider defaults and must not travel; the
    # documented default cap of 20 rounds does.
    captured: dict = {}
    rt = _ProbeRuntime(captured)
    rt.exec([{"type": "text", "text": "hi"}])
    assert captured["opts"] == {"max_iterations": 20}


def test_default_chat_completes_after_more_than_two_hundred_tool_rounds():
    # Each round asks for a different step: work that moves has no round
    # cap (identical rounds are the tool-loop guard's, test_tool_loop_guard).
    stream_fn, state = _make_stream_fn(
        [
            _assistant([ToolCall(id=f"call_{i}", name="echo", arguments={"step": i})])
            for i in range(205)
        ] + [_text_msg()]
    )
    session = _session(stream_fn)
    asyncio.run(session.run('Complete the whole task'))
    assert state['calls'] == 206


def test_completed_tool_round_returns_receipt_without_followup_generation():
    stream, state = _make_stream_fn([_tool_call_msg()])
    session = _session(stream, max_iterations=1, stop_after_tool_round=True)
    try:
        final = asyncio.run(session.run('Perform one step'))
        assert final.stop_reason == 'toolUse'
        assert not session.agent.state.error
        assert state['calls'] == 1
        receipts = [m for m in session.messages if isinstance(m, ToolResultMessage)]
        assert len(receipts) == 1 and not receipts[0].is_error
    finally:
        session.close()


def test_iteration_exhaustion_does_not_retry_completed_tool_effects():
    import pytest
    stream, state = _make_stream_fn([_tool_call_msg()])
    runtime = Runtime(call=lambda *_a, **_k: 'unused', max_retries=3)
    from openprogram.runtime.permissions.authority import local_owner_authority
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.agent.turn_request import (
        set_turn_request,
        reset_turn_request,
    )
    effects = []
    tool = _echo_tool()
    async def execute(*args):
        effects.append('echo')
        return await _echo_execute(*args)
    tool.execute = execute
    token = set_turn_request(TurnRequest(session_id='single-step', agent_id='main',
        user_text='Echo once', permission_mode='bypass', source='web', **local_owner_authority()))
    try:
        with pytest.raises(Exception, match='iteration limit') as caught:
            runtime.exec([{'type':'text','text':'step'}], tools=[tool],
                         stream_fn=stream, max_iterations=1)
        assert caught.value.retryable is False
        assert state['calls'] == 1
        assert effects == ['echo']
    finally:
        reset_turn_request(token)
        runtime.close()


def test_async_exec_publishes_explicit_single_tool_round():
    captured = {}
    class AsyncProbe(_ProbeRuntime):
        async def _async_call(self, content, model='default', response_format=None):
            captured['opts'] = _current_loop_opts.get()
            return 'ok'
    runtime = AsyncProbe(captured)
    try:
        asyncio.run(runtime.async_exec([{'type':'text','text':'step'}],
            max_iterations=1, stop_after_tool_round=True))
        assert captured['opts'] == {'max_iterations':1, 'stop_after_tool_round':True}
        assert _current_loop_opts.get() is None
    finally:
        runtime.close()


def test_single_round_rejects_final_output_contract_before_calling_provider():
    import pytest
    runtime = Runtime(call=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('provider called')))
    options = dict(content=[{'type':'text','text':'step'}], stop_after_tool_round=True,
                   response_format={'type':'object','properties':{},'additionalProperties':False})
    try:
        with pytest.raises(ValueError, match='cannot be combined'):
            runtime.exec(**options)
        with pytest.raises(ValueError, match='cannot be combined'):
            asyncio.run(runtime.async_exec(**options))
    finally:
        runtime.close()
