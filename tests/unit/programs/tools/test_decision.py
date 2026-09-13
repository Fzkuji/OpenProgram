"""agentic_programming.decision.make — the LLM picks the next step from a
set of options and ``decision.make`` resolves the pick.

Covered: a function option is picked (and run), a value option is
picked (its value is returned), and the dict ``(value, "desc")`` form.
"""

from __future__ import annotations

from openprogram.agentic_programming import decision
from openprogram.agentic_programming.runtime import Runtime


class _CannedRuntime(Runtime):
    """Runtime whose exec() always returns a fixed reply string."""

    def __init__(self, reply: str):
        super().__init__(call=lambda *a, **kw: reply, model="dummy")
        self._reply = reply

    def _call(self, content, model="default", response_format=None):
        return self._reply


def _greet(name: str) -> str:
    """Greet someone by name."""
    return f"hello {name}"


def _farewell() -> str:
    """Say goodbye."""
    return "bye"


def test_decide_runs_picked_function():
    # Dict form: the key is the option name shown to the model.
    rt = _CannedRuntime('{"call": "greet", "args": {"name": "ada"}}')
    result = decision.make("Pick one.", {"greet": _greet, "farewell": _farewell},
                    runtime=rt)
    assert result == "hello ada"


def test_decide_returns_value_option():
    rt = _CannedRuntime('{"call": "done"}')
    result = decision.make("Pick one.", {
        "greet": _greet,
        "done": "CONVERSATION_OVER",
    }, runtime=rt)
    assert result == "CONVERSATION_OVER"


def test_decide_value_option_with_description():
    rt = _CannedRuntime('{"call": "done"}')
    result = decision.make("Pick one.", {
        "greet": _greet,
        "done": ("CONVERSATION_OVER", "对话结束时选这个"),
    }, runtime=rt)
    assert result == "CONVERSATION_OVER"


def test_decide_list_form_of_functions():
    rt = _CannedRuntime('{"call": "_farewell"}')
    result = decision.make("Pick one.", [_greet, _farewell], runtime=rt)
    assert result == "bye"


def test_exec_choices_resolves_final_pick():
    """exec(choices=...) runs a normal turn but the final reply is
    resolved against the menu — here a function option is picked."""
    rt = _CannedRuntime('{"call": "greet", "args": {"name": "ada"}}')
    result = rt.exec("Do the work, then pick.", choices={
        "greet": _greet,
        "farewell": _farewell,
    })
    assert result == "hello ada"


def test_exec_choices_value_option():
    rt = _CannedRuntime('done picked: {"call": "stop"}')
    result = rt.exec("Work then finish.", choices={
        "greet": _greet,
        "stop": "STOPPED",
    })
    assert result == "STOPPED"


def test_exec_without_choices_returns_raw_text():
    rt = _CannedRuntime("just some text")
    assert rt.exec("hello") == "just some text"


def test_decide_schema_option_returns_nested_struct():
    """A schema option lets the model fill an arbitrary nested structure;
    the filled struct comes back, no function runs."""
    rt = _CannedRuntime(
        '{"call": "emit_plan", "args": {'
        '"steps": [{"action": "click", "target": "OK"}], '
        '"rationale": "because"}}'
    )
    result = decision.make("Decide.", {
        "emit_plan": ("Return a structured plan.", {
            "steps": [{"action": str, "target": str}],
            "rationale": str,
        }),
        "abort": "ABORTED",
    }, runtime=rt)
    assert result == {
        "decision": "emit_plan",
        "steps": [{"action": "click", "target": "OK"}],
        "rationale": "because",
    }


def test_decide_schema_option_validates_nested():
    """A nested field of the wrong type fails validation (with no retries,
    surfaces as ValueError)."""
    import pytest
    rt = _CannedRuntime(
        '{"call": "emit_plan", "args": {"steps": [{"action": 123, '
        '"target": "OK"}], "rationale": "x"}}'
    )
    with pytest.raises(ValueError):
        decision.make("Decide.", {
            "emit_plan": ("Plan.", {
                "steps": [{"action": str, "target": str}],
                "rationale": str,
            }),
        }, runtime=rt, max_retries=0)


def test_decide_raises_decisionerror_when_retries_exhausted():
    """An unresolvable reply raises DecisionError (a ValueError subclass),
    so a caller can catch it precisely."""
    import pytest
    from openprogram.agentic_programming.decision import DecisionError

    rt = _CannedRuntime("not json at all, no pick here")
    with pytest.raises(DecisionError):
        decision.make("Pick one.", [_greet, _farewell], runtime=rt, max_retries=0)
    # Backwards-compatible: still catchable as ValueError.
    with pytest.raises(ValueError):
        decision.make("Pick one.", [_greet, _farewell], runtime=rt, max_retries=0)


def test_decide_picks_up_ambient_runtime_inside_agentic_function():
    """Inside an @agentic_function, decision.make() needs no runtime= — it reads
    the ambient runtime the decorator installs."""
    from openprogram.agentic_programming.function import agentic_function

    rt = _CannedRuntime('{"call": "_farewell"}')

    @agentic_function
    def router(runtime=None):
        return decision.make("Pick one.", [_greet, _farewell])

    assert router(runtime=rt) == "bye"
