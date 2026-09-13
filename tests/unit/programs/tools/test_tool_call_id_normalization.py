"""Cross-provider tool-call id replay.

History recorded by one provider gets replayed to another when the user
switches models mid-session. Every provider path must keep ids within the
target protocol's limits (OpenAI caps call_id at 64 chars; Anthropic at 64
alphanum/_/-) while preserving the assistant-call ↔ tool-result pairing.

The regression that motivated this file: Responses-path turns store
composite ids ("call_x|fc_y", ~83 chars); the Chat Completions builder
replayed them verbatim, and an OpenAI-compatible relay (frontier) that
re-posts to the Responses API upstream got a 400
"call_id: string too long (83 > 64)".
"""
import re

from openprogram.providers.types import (
    AssistantMessage,
    Context,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

# Real-shape composite id from a Responses turn — 83 chars.
COMPOSITE_ID = (
    "call_aDOztGFmfRDuf8AfmnTBkKv8"
    "|fc_0cf2f197df406312016a54668856f4819b927912880086b383"
)
LONG_FLAT_ID = "x" * 80  # no separator, still over the 64-char cap


def _model(provider: str, api: str, model_id: str) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api=api,
        provider=provider,
        base_url="https://example.invalid/v1",
    )


def _history(tool_call_id: str, *, provider: str, api: str, model_id: str):
    """user → assistant(toolCall) → toolResult, ids paired."""
    return [
        UserMessage(role="user", content="hi", timestamp=1),
        AssistantMessage(
            role="assistant",
            content=[ToolCall(type="toolCall", id=tool_call_id, name="bash",
                              arguments={"command": "true"})],
            api=api,
            provider=provider,
            model=model_id,
            timestamp=2,
        ),
        ToolResultMessage(
            role="toolResult",
            tool_call_id=tool_call_id,
            tool_name="bash",
            content=[TextContent(type="text", text="ok")],
            timestamp=3,
        ),
    ]


# ---------------------------------------------------------------- completions

def _completions_ids(history, target: Model) -> tuple[str, str]:
    from openprogram.providers.openai_completions.openai_completions import (
        _build_messages,
    )
    msgs = _build_messages(Context(messages=history), target)
    call_ids = [tc["id"] for m in msgs if m.get("tool_calls")
                for tc in m["tool_calls"]]
    result_ids = [m["tool_call_id"] for m in msgs if m.get("role") == "tool"]
    assert len(call_ids) == 1 and len(result_ids) == 1
    return call_ids[0], result_ids[0]


def test_completions_splits_composite_responses_id():
    target = _model("frontier-intelligence", "openai-completions", "gpt-5.6-sol")
    history = _history(COMPOSITE_ID, provider="openai",
                       api="openai-responses", model_id="gpt-5.5")
    call_id, result_id = _completions_ids(history, target)
    assert call_id == result_id == COMPOSITE_ID.split("|")[0]
    assert len(call_id) <= 64 and "|" not in call_id


def test_completions_passes_short_foreign_id_through():
    target = _model("frontier-intelligence", "openai-completions", "gpt-5.6-sol")
    history = _history("toolu_01AbCdEfGh", provider="anthropic",
                       api="anthropic-messages", model_id="claude-opus-4-8")
    call_id, result_id = _completions_ids(history, target)
    assert call_id == result_id == "toolu_01AbCdEfGh"


def test_completions_hashes_overlong_flat_id_consistently():
    target = _model("frontier-intelligence", "openai-completions", "gpt-5.6-sol")
    history = _history(LONG_FLAT_ID, provider="weird",
                       api="openai-completions", model_id="m")
    call_id, result_id = _completions_ids(history, target)
    assert call_id == result_id
    assert call_id.startswith("tc_") and len(call_id) <= 64
    assert re.fullmatch(r"[a-zA-Z0-9_-]+", call_id)


# ------------------------------------------------------------------ responses

def _responses_items(history, target: Model):
    from openprogram.providers._shared.openai_responses import (
        convert_responses_messages,
    )
    items = convert_responses_messages(target, Context(messages=history))
    calls = [i for i in items if i.get("type") == "function_call"]
    outs = [i for i in items if i.get("type") == "function_call_output"]
    assert len(calls) == 1 and len(outs) == 1
    return calls[0], outs[0]


def test_responses_custom_relay_normalizes_composite_id():
    # Custom Responses-protocol relay (NOT in the old provider whitelist):
    # composite ids from an official-OpenAI turn must still be normalized.
    target = _model("my-relay", "openai-responses", "some-model")
    history = _history(COMPOSITE_ID, provider="openai",
                       api="openai-responses", model_id="gpt-5.5")
    fc, out = _responses_items(history, target)
    assert fc["call_id"] == out["call_id"]
    assert len(fc["call_id"]) <= 64
    assert re.fullmatch(r"[a-zA-Z0-9_-]+", fc["call_id"])
    # Foreign turn: the fc_ item id belongs to another upstream — dropped.
    assert "id" not in fc


def test_responses_same_model_keeps_native_ids():
    target = _model("openai", "openai-responses", "gpt-5.5")
    history = _history(COMPOSITE_ID, provider="openai",
                       api="openai-responses", model_id="gpt-5.5")
    fc, out = _responses_items(history, target)
    call_part, item_part = COMPOSITE_ID.split("|", 1)
    assert fc["call_id"] == out["call_id"] == call_part
    assert fc["id"] == item_part  # same model → item id replays verbatim


def test_responses_hashes_overlong_flat_id_consistently():
    target = _model("openai", "openai-responses", "gpt-5.5")
    history = _history(LONG_FLAT_ID, provider="weird",
                       api="openai-completions", model_id="m")
    fc, out = _responses_items(history, target)
    assert fc["call_id"] == out["call_id"]
    assert fc["call_id"].startswith("tc_") and len(fc["call_id"]) <= 64


def test_responses_short_foreign_id_passes_through():
    target = _model("openai", "openai-responses", "gpt-5.5")
    history = _history("toolu_01AbCdEfGh", provider="anthropic",
                       api="anthropic-messages", model_id="claude-opus-4-8")
    fc, out = _responses_items(history, target)
    assert fc["call_id"] == out["call_id"] == "toolu_01AbCdEfGh"
