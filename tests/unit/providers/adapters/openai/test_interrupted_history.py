"""Request projections repair interrupted history without executing tools."""
import json
import pytest

from openprogram.models.providers.shared.openai_responses import convert_responses_messages
from openprogram.models.providers.shared.transform_messages import transform_messages
from openprogram.models.providers.types import (
    AssistantMessage, Context, Model, TextContent, ToolCall, ToolResultMessage, UserMessage,
)


def model():
    return Model(id="m", name="m", provider="relay", api="openai-responses",
                 base_url="https://example.invalid/v1")


def call(*ids, stop_reason="toolUse", provider="relay"):
    return AssistantMessage(content=[TextContent(text="Saved partial answer"), *[
        ToolCall(id=key, name="write", arguments={"path": key}) for key in ids]],
        model="m", provider=provider, api="openai-responses", stop_reason=stop_reason, timestamp=10)


def result(key, text="saved real output", **kwargs):
    return ToolResultMessage(tool_call_id=key, tool_name="write",
                             content=[TextContent(text=text)], timestamp=20, **kwargs)


def test_responses_repairs_trailing_call_with_unknown_output():
    history = [call("call_a")]
    before = [msg.model_dump() for msg in history]
    wire = convert_responses_messages(model(), Context(messages=history))
    outputs = [item for item in wire if item.get("type") == "function_call_output"]
    assert len(outputs) == 1
    assert outputs[0]["call_id"] == "call_a"
    assert "unknown" in outputs[0]["output"]
    assert [msg.model_dump() for msg in history] == before


@pytest.mark.parametrize("stop_reason", ["toolUse", "error", "aborted"])
def test_saved_results_win_over_repair_and_stay_paired_before_user(stop_reason):
    history = [call("a", "b", stop_reason=stop_reason),
               UserMessage(content="Continue checking", timestamp=15), result("b")]
    before = [msg.model_dump() for msg in history]
    repaired = transform_messages(history, model())
    assert [msg.role for msg in repaired] == ["assistant", "toolResult", "toolResult", "user"]
    assert [msg.tool_call_id for msg in repaired[1:3]] == ["a", "b"]
    assert repaired[1].is_error and "unknown" in repaired[1].content[0].text
    assert repaired[2] == history[-1]
    assert "Saved partial answer" in " ".join(b.text for b in repaired[0].content if isinstance(b, TextContent))
    if stop_reason != "toolUse":
        assert "interrupted" in " ".join(b.text for b in repaired[0].content if isinstance(b, TextContent))
    assert transform_messages(repaired, model()) == repaired
    assert [msg.model_dump() for msg in history] == before


def test_late_real_result_replaces_placeholder_without_duplicates():
    repaired = transform_messages([call("a")], model())
    late = result("a", is_error=True)
    history = [*repaired, UserMessage(content="Check it", timestamp=30), late, late.model_copy()]
    projected = transform_messages(history, model())
    outputs = [msg for msg in projected if msg.role == "toolResult"]
    assert outputs == [late]
    assert projected[-1].content == "Check it"


def test_reused_ids_are_scoped_to_nearest_call_and_orphans_are_not_sent():
    first, second = result("a", "first"), result("a", "second")
    history = [result("orphan"), call("a"), first, first.model_copy(), call("a"), second]
    projected = transform_messages(history, model())
    assert [msg.role for msg in projected] == ["assistant", "toolResult", "assistant", "toolResult"]
    assert projected[1] == first and projected[3] == second
    assert history[0].tool_call_id == "orphan"


def test_reused_cross_model_id_does_not_rewrite_same_model_result():
    history = [call("a", provider="foreign"), result("a"), call("a"), result("a")]
    projected = transform_messages(history, model(), lambda key, *_: "normalized_" + key)
    assert projected[0].content[1].id == projected[1].tool_call_id == "normalized_a"
    assert projected[2].content[1].id == projected[3].tool_call_id == "a"


def test_conflicting_duplicate_call_ids_are_not_guessed():
    history = [call("a", "a")]
    with pytest.raises(ValueError, match="duplicate tool call"):
        transform_messages(history, model())


@pytest.mark.parametrize("adapter", ["responses", "completions", "anthropic", "google", "bedrock"])
def test_provider_wire_formats_preserve_one_output_per_repaired_call(adapter):
    target = model()
    context = Context(messages=[call("call_a", "call_b", stop_reason="aborted"), result("call_b")])
    if adapter == "responses":
        wire = convert_responses_messages(target, context)
        calls = [item["call_id"] for item in wire if item.get("type") == "function_call"]
        outputs = [item["call_id"] for item in wire if item.get("type") == "function_call_output"]
    elif adapter == "google":
        from openprogram.models.providers.shared.google import convert_messages
        wire = convert_messages(target, context)
        calls = [part["functionCall"]["name"] for item in wire for part in item["parts"] if "functionCall" in part]
        outputs = [part["functionResponse"]["name"] for item in wire for part in item["parts"] if "functionResponse" in part]
    elif adapter == "bedrock":
        from openprogram.models.providers.adapters.amazon_bedrock.amazon_bedrock import _convert_messages_bedrock
        wire = _convert_messages_bedrock(context, target, "none")
        calls = [part["toolUse"]["toolUseId"] for item in wire for part in item["content"] if "toolUse" in part]
        outputs = [part["toolResult"]["toolUseId"] for item in wire for part in item["content"] if "toolResult" in part]
    else:
        repaired = Context(messages=transform_messages(context.messages, target))
        if adapter == "anthropic":
            from openprogram.models.providers.adapters.anthropic.anthropic import _build_messages
            wire = _build_messages(repaired)
            calls = [part["id"] for item in wire for part in item["content"] if part.get("type") == "tool_use"]
            outputs = [part["tool_use_id"] for item in wire for part in item["content"] if part.get("type") == "tool_result"]
        else:
            from openprogram.models.providers.adapters.openai_completions.openai_completions import _build_messages
            wire = _build_messages(repaired, target)
            calls = [call["id"] for item in wire for call in item.get("tool_calls", [])]
            outputs = [item["tool_call_id"] for item in wire if item.get("role") == "tool"]
    assert len(calls) == len(outputs) == 2
    assert calls == outputs
    assert json.dumps(wire).count("outcome is unknown") == 1
    assert json.dumps(wire).count("saved real output") == 1
