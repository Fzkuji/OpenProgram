"""The trial's model: one tool call, then a final answer."""
from __future__ import annotations

import json
import urllib.request

import pytest

from openprogram.operations.self_update.trial.fake_model import FINAL_ANSWER, TURN_MARKER, FakeModel


def _post(model: FakeModel, messages: list, tools: list | None = None, stream: bool = False):
    request = urllib.request.Request(
        f"{model.base_url}/chat/completions", method="POST",
        data=json.dumps({"messages": messages, "tools": tools or [], "stream": stream}).encode(),
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        return response.read().decode()


@pytest.fixture
def model():
    with FakeModel() as running:
        yield running


TOOLS = [{"type": "function", "function": {"name": "list"}}]


def test_a_turn_gets_one_tool_call_and_then_the_final_answer(model):
    first = json.loads(_post(model, [{"role": "user", "content": TURN_MARKER}], TOOLS))
    call = first["choices"][0]["message"]["tool_calls"][0]
    assert call["function"]["name"] == "list" and first["choices"][0]["finish_reason"] == "tool_calls"
    second = json.loads(_post(model, [{"role": "user", "content": TURN_MARKER},
                                      {"role": "assistant", "tool_calls": [call]},
                                      {"role": "tool", "content": "result", "tool_call_id": call["id"]}], TOOLS))
    assert second["choices"][0]["message"]["content"] == FINAL_ANSWER
    assert [item["kind"] for item in model.turn_requests()] == ["tool_call", "final"]
    assert model.turn_requests()[1]["tool_result"] == "result"


def test_any_other_request_gets_a_short_text(model):
    reply = json.loads(_post(model, [{"role": "user", "content": "make a title"}], TOOLS))
    assert reply["choices"][0]["message"]["content"] == "ok"
    assert model.turn_requests() == []


def test_it_streams(model):
    body = _post(model, [{"role": "user", "content": TURN_MARKER}], TOOLS, stream=True)
    assert '"tool_calls"' in body and body.rstrip().endswith("data: [DONE]")
