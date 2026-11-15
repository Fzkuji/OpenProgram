"""Agent tool adaptation keeps instance configuration and executors separate."""
import asyncio
import inspect
import importlib
import itertools

import pytest

from openprogram import Agent
from openprogram.runtime.agent.runtime.shared import _adapt_tools


@pytest.mark.parametrize("asynchronous", [False, True])
def test_adapted_method_keeps_its_instance_after_second_registration(asynchronous):
    class ToolAgent(Agent):
        method_options = {"identify": {"tool": True, "register_globally": False}}

        if asynchronous:
            async def identify(self, suffix: str):
                await asyncio.sleep(0)
                return self.identity + suffix
        else:
            def identify(self, suffix: str):
                return self.identity + suffix

    first = ToolAgent()
    first.identity = "first:"
    adapted_before = _adapt_tools([first.identify])[0]
    second = ToolAgent()
    second.identity = "second:"
    adapted_second = _adapt_tools([second.identify])[0]
    adapted_after = _adapt_tools([first.identify])[0]

    async def dispatch():
        results = []
        for tool in (adapted_before, adapted_second, adapted_after):
            result = await tool.execute("call", {"suffix": "value"}, None, None)
            results.append(result.content[0].text)
        return results

    assert asyncio.run(dispatch()) == ["first:value", "second:value", "first:value"]
    assert first.identify._agent_tool is not second.identify._agent_tool
    assert list(inspect.signature(first.identify).parameters) == ["suffix"]
    assert list(first.identify.spec["parameters"]["properties"]) == ["suffix"]
    assert list(first.identify._agent_tool.parameters["properties"]) == ["suffix"]


def test_unconfigured_child_keeps_parent_model_instructions_and_denial():
    from openprogram import Runtime
    from openprogram.runtime.agent import agent
    from openprogram.runtime.agent.runtime.shared import (
        _current_instructions, _current_tool_policy,
    )

    requests = []

    def provider(content, model=None, **kwargs):
        requests.append((model, _current_instructions.get(),
                         (_current_tool_policy.get() or {}).get("deny")))
        return "completed"

    class Child(Agent):
        def run(self):
            return agent("request", tools_deny=[])

    class Parent(Agent):
        def run(self):
            return Child().run()

    runtime = Runtime(call=provider)
    try:
        parent = Parent(runtime=runtime, model="parent-model", tools=[],
                        instructions="parent instructions", tools_deny=["bash"])
        assert parent.run() == "completed"
        assert requests == [("parent-model", "parent instructions", ["bash"])]
        assert _current_instructions.get() is None
        assert _current_tool_policy.get() is None
    finally:
        runtime.close()


@pytest.mark.parametrize("override", ["unset", "call", None])
def test_agent_context_overrides_ambient_content_at_public_call(override):
    import json
    from openprogram import Context, Runtime

    ambient = Context({"topic": "ambient", "outer": "inherited"}, history="thread")
    configured = Context({"topic": "instance", "inner": "configured"})
    observations = []

    def provider(content, **kwargs):
        context = Context.current()
        observations.append(context.history)
        return json.dumps(context.resolve_blocks())

    runtime = Runtime(call=provider)
    instance = Agent(context=configured, runtime=runtime, tools=[])
    kwargs = {} if override == "unset" else {
        "context": Context({"topic": "call"}) if override == "call" else None,
    }
    try:
        with ambient.bind():
            result = json.loads(instance("request", **kwargs))
            assert Context.current() is ambient
        expected = {"topic": "ambient", "outer": "inherited"}
        if override is not None:
            expected.update(topic="call" if override == "call" else "instance", inner="configured")
        assert result == expected
        assert observations == ["thread"]
        assert configured.resolve_blocks() == {"topic": "instance", "inner": "configured"}
        assert ambient.resolve_blocks() == {"topic": "ambient", "outer": "inherited"}
    finally:
        runtime.close()



@pytest.mark.parametrize("names", list(itertools.permutations(("Agent", "Runtime", "agent"))))
def test_public_agent_entry_and_internal_package_support_import_order(names, monkeypatch):
    import openprogram

    # Exercise lazy public entry lookup after any prior internal package import.
    monkeypatch.delattr(openprogram, "agent", raising=False)
    namespace = {}
    for name in names:
        exec(f"from openprogram import {name}", namespace)
    entry = importlib.import_module("openprogram.runtime.agent.agent_function")
    assert namespace["agent"] is entry.agent
    runtime = namespace["Runtime"](call=lambda content, **kwargs: "completed")
    try:
        assert namespace["agent"]("request", runtime=runtime, tools=[]) == "completed"
    finally:
        runtime.close()



def test_unavailable_tool_method_still_records_direct_python_call():
    from openprogram.runtime.agent.call_state import current_call_id

    class ToolAgent(Agent):
        method_options = {"run": {"tool": True, "available_if": lambda: False}}

        def run(self):
            return current_call_id()

    instance = ToolAgent()
    assert instance.run()
    assert "run" not in instance._method_tools
    assert current_call_id() == ""



def test_ordinary_method_injects_required_runtime_without_method_options():
    from openprogram import Runtime

    class PlainAgent(Agent):
        def run(self, runtime):
            return runtime.exec(content=[{"type": "text", "text": "request"}], tools=[])

    runtime = Runtime(call=lambda content, **kwargs: "completed")
    try:
        assert PlainAgent(runtime=runtime).run() == "completed"
    finally:
        runtime.close()


def test_tool_alias_and_custom_schema_are_used_by_adapted_entry():
    class AliasAgent(Agent):
        method_options = {"run": {
            "tool": True, "register_globally": False, "name": "custom_alias",
            "description": "Custom description.",
            "input": {"secret": {"hidden": True}},
        }}

        def run(self, public: str, secret: str = "hidden", runtime=None):
            return public

    instance = AliasAgent()
    adapted = _adapt_tools([instance.run])[0]
    assert adapted.name == "custom_alias"
    assert adapted.description == "Custom description."
    assert set(adapted.parameters["properties"]) == {"public"}
    assert instance.run._agent_tool._python_callable is instance.run
    assert instance.run._agent_tool._method_options.name == "custom_alias"



def test_unused_runtime_observations_do_not_create_a_provider(monkeypatch):
    from openprogram.runtime.agent.runtime_scope import _LazyRuntime

    def unexpected_provider(**kwargs):
        raise AssertionError("Observing an unused runtime must not create a provider")

    monkeypatch.setattr("openprogram.runtime.agent.runtime.factory.create_runtime", unexpected_provider)
    runtime = _LazyRuntime()
    assert runtime.last_blocks == []
    assert runtime.last_usage is None
    assert runtime.last_agent_iteration_count == 0
    assert runtime.usage_is_cumulative is False
    assert runtime.api_model is None
    assert runtime.provider_id is None
    assert runtime.model == 'default'
    assert runtime.thinking_level == 'off'
    assert runtime.can_ask() is False
    assert runtime.on_stream is None
    assert runtime._skills_config is None
    assert not hasattr(runtime, 'session_id')
    with pytest.raises(AssertionError, match="Observing"):
        runtime.exec(content=[])


def test_managed_plain_function_uses_shared_runtime_injection():
    from openprogram import Runtime
    from openprogram.runtime.agent.call_scope import managed_function
    from openprogram.runtime.agent.call_state import _current_runtime

    def entry(runtime, review_runtime=None):
        assert runtime is review_runtime
        assert _current_runtime.get() is runtime
        return runtime.exec(content=[{"type": "text", "text": "request"}], tools=[])

    runtime = Runtime(call=lambda content, **kwargs: "completed")
    try:
        assert managed_function(entry)(runtime=runtime) == "completed"
        assert _current_runtime.get() is None
    finally:
        runtime.close()


def test_decision_parses_bound_method_without_receiver_argument():
    from openprogram import Runtime
    from openprogram.runtime.agent.decision import parse_args

    class DecisionAgent(Agent):
        method_options = {"run": {"tool": True, "register_globally": False}}

        def run(self, text: str, runtime):
            assert runtime is selected_runtime
            return self.identity + text

    selected_runtime = Runtime(call=lambda content, **kwargs: "unused")
    try:
        instance = DecisionAgent(runtime=selected_runtime)
        instance.identity = "owner:"
        target, arguments = parse_args(
            '{"call":"run","args":{"text":"value"}}',
            [instance.run], selected_runtime,
        )
        assert target(**arguments) == "owner:value"
        assert instance.run._fn(text="raw", runtime=selected_runtime) == "owner:raw"
        assert "self" not in inspect.signature(instance.run._fn).parameters
    finally:
        selected_runtime.close()


def test_static_tool_source_has_no_bound_receiver():
    class StaticAgent(Agent):
        method_options = {"run": {"tool": True, "register_globally": False}}

        @staticmethod
        def run(text: str):
            return text

    instance = StaticAgent()
    assert instance.run._agent_owner is None
    assert instance.run._fn("value") == "value"



def test_same_method_name_on_different_agents_does_not_share_recursion_count():
    class Leaf(Agent):
        def run(self):
            return "completed"

    class E(Agent):
        def run(self):
            return Leaf().run()

    class D(Agent):
        def run(self):
            return E().run()

    class C(Agent):
        def run(self):
            return D().run()

    class B(Agent):
        def run(self):
            return C().run()

    class A(Agent):
        def run(self):
            return B().run()

    assert A().run() == "completed"


def test_actual_method_recursion_remains_bounded_and_restores_state():
    from openprogram.runtime.agent.call_state import _recursion_depth

    class Recursive(Agent):
        def run(self, recurse=True):
            return self.run() if recurse else "completed"

    instance = Recursive()
    with pytest.raises(RecursionError, match="max nesting depth"):
        instance.run()
    assert _recursion_depth.get() is None
    assert instance.run(recurse=False) == "completed"
