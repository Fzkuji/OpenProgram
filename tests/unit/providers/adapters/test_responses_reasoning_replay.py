"""Stored Responses reasoning output must be valid when reused as input."""
import json

import pytest

from openprogram.providers._shared.openai_responses import convert_responses_messages
from openprogram.providers.types import AssistantMessage, Context, Model, ThinkingContent


@pytest.mark.parametrize("encrypted", [None, "opaque"])
@pytest.mark.parametrize("content", [None, [{"type": "reasoning_text", "text": "detail"}]])
def test_reasoning_replay_omits_null_optional_fields_without_mutating_history(content, encrypted):
    model = Model(id="m", name="m", provider="relay", api="openai-responses",
                  base_url="https://example.invalid/v1")
    signature = json.dumps({"type": "reasoning", "id": "rs_1", "summary": [],
                            "content": content, "encrypted_content": encrypted, "status": None})
    block = ThinkingContent(type="thinking", thinking="", thinking_signature=signature)
    message = AssistantMessage(role="assistant", content=[block], model=model.id,
                               provider=model.provider, api=model.api, timestamp=1)
    items = convert_responses_messages(model, Context(messages=[message]))
    expected = {"type": "reasoning", "summary": []}
    if encrypted is not None:
        expected["encrypted_content"] = encrypted
    if content is not None:
        expected["content"] = content
    assert items == [expected]
    assert message.content[0].thinking_signature == signature
