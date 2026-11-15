from __future__ import annotations




def test_agent_forwards_bounded_tool_loop_controls_and_can_return_raw_result():
    from openprogram.runtime.agent.agent_function import agent

    calls = []

    class Runtime:
        def exec(self, **kwargs):
            calls.append(kwargs)
            return {"text": "finished", "tool_result": {"ok": True}}

    tool = object()
    result = agent(
        "perform one browser action",
        tools=[tool],
        tool_choice={"type": "function", "name": "browser_page"},
        parallel_tool_calls=False,
        max_iterations=1,
        timeout_s=30,
        execution_kind="browser_agent",
        runtime=Runtime(),
        return_raw=True,
    )

    assert result == {"text": "finished", "tool_result": {"ok": True}}
    assert calls == [{
        "content": [{"type": "text", "text": "perform one browser action"}],
        "model": None,
        "tools": [tool],
        "tool_choice": {"type": "function", "name": "browser_page"},
        "parallel_tool_calls": False,
        "max_iterations": 1,
        "timeout_s": 30,
        "effort": None,
        "execution_kind": "browser_agent",
    }]


def test_agent_keeps_text_return_contract_by_default():
    from openprogram.runtime.agent.agent_function import agent

    class Runtime:
        def exec(self, **_kwargs):
            return {"text": "finished", "other": "metadata"}

    assert agent("work", runtime=Runtime()) == "finished"


def test_agent_choices_return_the_resolved_pick_after_the_tool_loop():
    from openprogram.runtime.agent.agent_function import agent

    calls = []
    choices = {"close": {"status": "closed"}}

    class Runtime:
        def exec(self, **kwargs):
            calls.append(kwargs)
            return {"status": "closed"}

    assert agent("handle the ticket", toolset="default", choices=choices,
                 runtime=Runtime()) == {"status": "closed"}
    assert calls[0]["choices"] is choices
    assert calls[0]["toolset"] == "default"


def test_standalone_agent_async_selects_explicit_provider_before_construction(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from openprogram.runtime.agent import agent_async
    from openprogram.runtime.agent.call_state import _current_runtime
    from openprogram.state.store import _store

    selections = []
    closed = []

    class FakeRuntime:
        async def async_exec(self, **kwargs):
            assert kwargs['model'] == 'chosen:model-id'
            return {'text': 'selected'}

        def close(self):
            closed.append(True)

    def create(**kwargs):
        selections.append(kwargs)
        return FakeRuntime()

    monkeypatch.setattr('openprogram.runtime.agent.runtime.factory.create_runtime', create)
    runtime_token = _current_runtime.set(None)
    store_token = _store.set(SimpleNamespace(session_id='selection-test'))
    try:
        assert asyncio.run(agent_async('hello', model='chosen:model-id')) == 'selected'
        assert selections == [{'provider': 'chosen', 'model': 'model-id'}]
        assert closed == [True]
        assert _current_runtime.get() is None
    finally:
        _store.reset(store_token)
        _current_runtime.reset(runtime_token)
