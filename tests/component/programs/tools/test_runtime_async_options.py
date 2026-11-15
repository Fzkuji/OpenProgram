"""Async execution uses the same request options and cleanup as sync execution."""
import asyncio

import pytest

from openprogram.runtime.agent.runtime import Runtime
from openprogram.runtime.agent.runtime.shared import (
    _current_call_model, _current_effort, _current_loop_opts,
    _current_tool_policy, _current_tools,
)
from openprogram.models.providers.utils.errors import ErrorReason, LLMError


def test_async_request_options_are_bound_and_restored():
    seen = []

    async def call(content, **kwargs):
        seen.append((_current_tools.get(), _current_effort.get(),
                     _current_tool_policy.get(), _current_loop_opts.get(),
                     _current_call_model.get()))
        return "ok"

    runtime = Runtime(call=call, model="dummy")
    outer_tools = ["outer"]
    token = _current_tools.set(outer_tools)
    try:
        assert asyncio.run(runtime.async_exec(
            "question", tools=[], tools_deny=["bash"], effort="high",
            max_iterations=2, parallel_tool_calls=False,
        )) == "ok"
        assert _current_tools.get() is outer_tools
        assert seen == [([], "high", {"deny": ["bash"]},
                         {"max_iterations": 2, "parallel_tool_calls": False}, "dummy")]
    finally:
        _current_tools.reset(token)
        runtime.close()


def test_async_timeout_cancels_request_and_closes_node():
    finished = []
    closed = []

    async def call(content, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            finished.append(True)

    runtime = Runtime(call=call, model="dummy")
    runtime._close_model_call_node = lambda node_id, **kwargs: closed.append(kwargs)
    try:
        with pytest.raises(LLMError) as error:
            asyncio.run(runtime.async_exec("question", timeout_s=0.01))
        assert error.value.reason == ErrorReason.TIMEOUT
        assert finished == [True]
        assert closed[-1]["status"] == "error"
    finally:
        runtime.close()


def test_async_cancellation_records_cancelled_and_restores_options():
    closed = []

    async def scenario():
        started = asyncio.Event()

        async def call(content, **kwargs):
            started.set()
            await asyncio.Event().wait()

        runtime = Runtime(call=call, model="dummy")
        runtime._close_model_call_node = lambda node_id, **kwargs: closed.append(kwargs)
        try:
            task = asyncio.create_task(runtime.async_exec("question", tools=[]))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert closed[-1]["status"] == "cancelled"
        finally:
            runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("asynchronous", [False, True])
def test_agent_defaults_apply_to_runtime_and_explicit_values_override(asynchronous):
    from openprogram.runtime.agent.runtime.shared import _current_agent_options

    seen = []

    def capture(content, **kwargs):
        seen.append((_current_tools.get(), _current_effort.get(),
                     _current_loop_opts.get(), _current_call_model.get()))
        return "ok"

    async def async_capture(content, **kwargs):
        return capture(content, **kwargs)

    runtime = Runtime(call=async_capture if asynchronous else capture, model="dummy")
    configured_tools = [{"spec": {"name": "lookup", "description": "Read",
                                  "parameters": {"type": "object", "properties": {}}},
                         "execute": lambda: "evidence"}]
    token = _current_agent_options.set(dict(
        model="configured", tools=configured_tools, effort="high", max_iterations=3,
    ))
    try:
        if asynchronous:
            asyncio.run(runtime.async_exec("question"))
            asyncio.run(runtime.async_exec("question", tools=[], effort="", max_iterations=1))
        else:
            runtime.exec("question")
            runtime.exec("question", tools=[], effort="", max_iterations=1)
        assert seen == [(configured_tools, "high", {"max_iterations": 3}, "configured"),
                        ([], "", {"max_iterations": 1}, "configured")]
    finally:
        _current_agent_options.reset(token)
        runtime.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_runtime_call_cannot_clear_outer_tool_constraints(asynchronous):
    from openprogram.runtime.agent.runtime.shared import _current_agent_options

    seen = []

    def capture(content, **kwargs):
        seen.append(_current_tool_policy.get())
        return "ok"

    async def async_capture(content, **kwargs):
        return capture(content, **kwargs)

    runtime = Runtime(call=async_capture if asynchronous else capture, model="dummy")
    token = _current_agent_options.set(dict(tools_deny=["bash"], tools_allow=["read*"]))
    try:
        kwargs = dict(tools=[], tools_deny=[], tools_allow=["*file"])
        if asynchronous:
            asyncio.run(runtime.async_exec("question", **kwargs))
        else:
            runtime.exec("question", **kwargs)
        assert seen[0]["deny"] == ["bash"]
        assert seen[0]["allow_constraints"] == [["read*"], ["*file"]]
    finally:
        _current_agent_options.reset(token)
        runtime.close()
