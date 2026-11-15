"""One-shot Agent selection preserves the existing task scope."""
import pytest

from openprogram import Agent, Context, Runtime
from openprogram.runtime.agent.decision import DecisionError
from openprogram.runtime.agent.runtime.shared import (
    _current_agent_options, _current_instructions, _current_loop_opts, _current_tools,
)


def test_agent_choice_returns_option_id_once_and_inherits_context():
    seen = []

    def provider(content, model=None, **kwargs):
        current = Context.current()
        seen.append((model, current["topic"], current.call_id,
                     _current_instructions.get(), dict(_current_loop_opts.get() or {}), _current_tools.get()))
        return '{"call":"B"}'

    class Parent(Agent):
        instructions = None

        def run(self):
            return child.choose("Select a category.", {"A": "News", "B": "Question"})

    runtime = Runtime(call=provider)
    try:
        child = Agent(runtime=runtime, model="small", tools=["bash"],
                      max_iterations=20, web_search=True,
                      response_format={"type": "json_object"})
        parent = Parent(runtime=runtime, context=Context({"topic": "Incoming message"}),
                        instructions="Caller instruction")
        assert parent.run() == "B"
        assert len(seen) == 1
        model, topic, call_id, instructions, loop_options, tools = seen[0]
        assert (model, topic, instructions) == ("small", "Incoming message", "Caller instruction")
        assert call_id
        assert loop_options["max_iterations"] == 1
        assert loop_options.get("web_search", False) is False
        assert tools == []
        assert Context.current() is None
        assert _current_instructions.get() is None
        assert _current_agent_options.get() == {}
    finally:
        runtime.close()


def test_invalid_choice_does_not_repick():
    requests = []

    def provider(content, **kwargs):
        requests.append(content)
        return '{"call":"unknown"}'

    runtime = Runtime(call=provider)
    try:
        picker = Agent(runtime=runtime)
        with pytest.raises(DecisionError):
            picker.choose("Pick one.", {"A": "First", "B": "Second"})
        assert len(requests) == 1
        assert Context.current() is None
    finally:
        runtime.close()


@pytest.mark.parametrize("options", [{}, {"": "Empty ID"}, {"A": lambda: "executed"}, ["A", "B"]])
def test_invalid_options_do_not_call_the_model(options):
    requests = []
    runtime = Runtime(call=lambda *args, **kwargs: requests.append(args))
    try:
        with pytest.raises(ValueError):
            Agent(runtime=runtime).choose("Pick.", options)
        assert not requests
    finally:
        runtime.close()


@pytest.mark.parametrize("outer_attempts", [1, 3])
def test_choice_does_not_inherit_outer_schema_or_consume_its_budget(outer_attempts):
    observed, selections = [], []

    def inner_provider(content, **kwargs):
        observed.append(kwargs.get("response_format"))
        return '{"call":"B"}'

    inner = Runtime(call=inner_provider, max_retries=1)
    child = Agent(runtime=inner)

    def outer_provider(content, **kwargs):
        from openprogram.runtime.agent.runtime.shared import (
            _current_response_format, _current_model_call_budget,
        )
        outer_format = _current_response_format.get()
        outer_budget = _current_model_call_budget.get()
        selections.append(child.choose("Pick.", {"A": "First", "B": "Second"}))
        assert _current_response_format.get() is outer_format
        assert _current_model_call_budget.get() is outer_budget
        return '{"answer":7}'

    outer = Runtime(call=outer_provider, max_retries=outer_attempts)
    schema = {"type": "object", "properties": {"answer": {"type": "integer"}},
              "required": ["answer"], "additionalProperties": False}
    try:
        result = outer.exec("Produce outer result", tools=[], response_format={
            "type": "json_schema", "schema": schema,
            "fallback": "prompt", "max_validation_retries": 0,
        })
        assert result == {"answer": 7}
        assert observed == [None]
        assert selections == ["B"]
    finally:
        outer.close()
        inner.close()
