"""After a write, a self-check asks a question back and repairs a miss."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openprogram.runtime.conversation.memory_agent import _structured
from openprogram.state.memory.management import verification
from openprogram.state.memory.management.config import load_memory_config

SCHEMA = {
    "type": "object",
    "properties": {"supported": {"type": "boolean"}, "why": {"type": "string"}},
    "required": ["supported"],
}


def test_structured_reply_is_read_from_bare_fenced_or_wrapped_json():
    assert _structured('{"supported": true}', SCHEMA) == {"supported": True}
    assert _structured('```json\n{"supported": false}\n```', SCHEMA) == {"supported": False}
    assert _structured('Sure: {"supported": true, "why": "x"} done', SCHEMA)["why"] == "x"


def test_structured_reply_missing_or_mistyped_fields_is_none():
    assert _structured("no json here", SCHEMA) is None
    assert _structured('{"why": "x"}', SCHEMA) is None
    assert _structured('{"supported": "yes"}', SCHEMA) is None
    # A bool is an int in Python, but not an integer in JSON Schema.
    assert _structured('{"n": true}', {
        "type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"],
    }) is None


class ScriptedAgent:
    """Replies in order; a structured call gets its JSON parsed."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.stages = []

    def run(self, *, prompt, system_prompt, cwd, tools, max_turns,
            max_budget_usd, output_schema=None):
        reply = self.replies.pop(0)
        self.stages.append("structured" if output_schema else "agent")
        return SimpleNamespace(
            text=reply, reply=reply, turns=[], num_turns=1,
            input_tokens=0, output_tokens=0, stop_reason="stop",
            anthropic_equivalent_cost_usd=None,
            structured_output=_structured(reply, output_schema) if output_schema else None,
        )


PROBE = json.dumps({
    "question": "What transport does the API use?",
    "expected_answer": "gRPC",
    "refs": ["openprogram/s1/m1"],
})


@pytest.fixture
def root(tmp_path, monkeypatch):
    import openprogram.state.paths as paths
    from openprogram.state.memory import store

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    return store.ensure()


def _verify(root, agent):
    return verification.verify_batch(
        root, agent=agent, observation_date="2026-10-16",
        turns=[("Owner", "The API uses gRPC.")], refs=["openprogram/s1/m1"],
        config=load_memory_config({}),
    )


def test_a_fact_found_on_the_first_try_passes(root):
    agent = ScriptedAgent([PROBE, "<answer>gRPC</answer>", '{"supported": true}'])
    report = _verify(root, agent)
    assert report["outcome"] == "passed"
    assert report["initial_answer"] == "gRPC"
    assert agent.stages == ["structured", "agent", "structured"]


def test_a_miss_is_repaired_and_checked_again(root):
    agent = ScriptedAgent([
        PROBE, "<answer>unknown</answer>", '{"supported": false}',
        "reorganized",
        "<answer>gRPC</answer>", '{"supported": true}',
    ])
    report = _verify(root, agent)
    assert report["outcome"] == "repaired"
    assert report["final_answer"] == "gRPC"
    assert agent.stages == [
        "structured", "agent", "structured", "agent", "agent", "structured",
    ]


def test_a_missing_structured_reply_is_asked_again(root):
    agent = ScriptedAgent([
        "I think the question is about transport.", PROBE,
        "<answer>gRPC</answer>", '{"supported": true}',
    ])
    assert _verify(root, agent)["outcome"] == "passed"


def test_verification_never_fails_the_write_and_is_logged(root):
    other = json.dumps({**json.loads(PROBE), "refs": ["openprogram/other/m9"]})
    changed = verification.verify_after_write(
        root, agent=ScriptedAgent([other]), observation_date="2026-10-16",
        turns=[("Owner", "The API uses gRPC.")], refs=["openprogram/s1/m1"],
    )
    assert changed == []
    verification.verify_after_write(
        root,
        agent=ScriptedAgent([PROBE, "<answer>gRPC</answer>", '{"supported": true}']),
        observation_date="2026-10-16",
        turns=[("Owner", "The API uses gRPC.")], refs=["openprogram/s1/m1"],
    )
    status = verification.status(root)
    assert (status["error"], status["passed"]) == (1, 1)
    assert status["last"]["outcome"] == "passed"

    from openprogram.state.memory.retrieval import inspect
    assert inspect.status(root)["verification"]["passed"] == 1


def test_the_switch_is_off_unless_set():
    assert load_memory_config({}).writer_verify is False
    on = load_memory_config({"memory": {"writer": {"verify": True}}})
    assert on.writer_verify is True
