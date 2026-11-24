"""One size for a request everywhere it is asked (``token_accounting.measure``)."""
from __future__ import annotations

from types import SimpleNamespace

from openprogram.models.providers.types import (
    AssistantMessage, ServerToolUse, TextContent, ThinkingContent, Usage,
)
from openprogram.runtime.context.token_accounting import measure
from openprogram.runtime.context.token_accounting.tokens import estimate_message_tokens


def _reply(*blocks, prompt=50_000, stop="stop", cache_write=0):
    return AssistantMessage(
        content=[TextContent(text="ok"), *blocks], api="openai-responses", provider="x",
        model="m", stop_reason=stop, timestamp=0,
        usage=Usage(input=prompt, cache_write=cache_write, output=10, tokens_reported=True))


def test_the_size_is_the_last_count_plus_the_change():
    anchor = measure.anchor_from(_reply(prompt=52_000), 50_000, 256_000)
    assert anchor is not None and anchor.offset == 2_000
    assert measure.request_size(60_000, anchor) == 62_000
    assert measure.request_size(60_000, None) == 60_000


def test_a_reply_that_ran_provider_tools_is_no_anchor():
    """xAI and OpenAI report a reply's search passes summed: 3 passes read
    as three times the prompt."""
    search = ServerToolUse(id="ws", name="web_search", input={})
    assert measure.anchor_from(_reply(search, prompt=150_000), 50_000, 256_000) is None


def test_implausible_or_failed_counts_are_no_anchor():
    assert measure.anchor_from(_reply(prompt=300_000), 280_000, 256_000) is None
    assert measure.anchor_from(_reply(prompt=200_000), 60_000, 256_000) is None
    assert measure.anchor_from(_reply(stop="error"), 50_000, 256_000) is None


def test_a_cache_write_is_part_of_the_prompt():
    anchor = measure.anchor_from(_reply(prompt=1_000, cache_write=40_000), 40_000, 200_000)
    assert anchor is not None and anchor.measured == 41_000


def test_a_budget_takes_the_anchor_in_its_history_slot():
    budget = SimpleNamespace(system_prompt=1_000, tools_schema=2_000, history=10_000)
    measure.apply(budget, measure.Anchor(measured=15_000, estimate=13_000))
    assert budget.history == 12_000


def test_reasoning_sent_back_is_counted():
    """A reasoning model's replies go back with their reasoning; an estimate
    without it read the history a fifth short."""
    plain = _reply()
    reasoning = _reply(ThinkingContent(thinking="step " * 200, thinking_signature="x" * 4_000))
    assert estimate_message_tokens(reasoning) - estimate_message_tokens(plain) >= 1_000


def test_a_responses_reasoning_item_counts_its_encrypted_payload_once():
    """The stored item repeats its summary, which the block's text already
    counts; only the encrypted payload is added."""
    import json
    summary = "推理摘要" * 300
    item = json.dumps({"type": "reasoning", "summary": [{"text": summary}],
                       "encrypted_content": "e" * 8_000})
    plain = _reply()
    reasoning = _reply(ThinkingContent(thinking=summary, thinking_signature=item))
    with_text_only = _reply(ThinkingContent(thinking=summary))
    assert (estimate_message_tokens(reasoning) - estimate_message_tokens(with_text_only)) == 2_000
    assert estimate_message_tokens(with_text_only) > estimate_message_tokens(plain)
