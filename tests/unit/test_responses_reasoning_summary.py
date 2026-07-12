"""Codex reasoning summary must survive an empty-summary DONE item.

Codex (chatgpt backend) streams the readable reasoning text via
``response.reasoning_summary_text.delta`` events, then sends the final
``response.output_item.done`` reasoning item with ``encrypted_content``
and an EMPTY ``summary`` array. The parser used to overwrite the
delta-accumulated text with that empty join, so the thinking block was
"" at persist time and got dropped — the UI never got a "Thinking ×1"
strip, live or after refresh.
"""
import asyncio
from types import SimpleNamespace

from openprogram.providers._shared.openai_responses import process_responses_stream


class _Stream:
    def __init__(self):
        self.events = []

    def push(self, evt):
        self.events.append(evt)


async def _feed(events):
    for e in events:
        yield e


def _run(events):
    output = SimpleNamespace(content=[])
    stream = _Stream()
    asyncio.run(process_responses_stream(
        _feed(events), output, stream, model=SimpleNamespace(id="gpt-5.6-luna"),
    ))
    return output, stream


def test_empty_done_summary_keeps_delta_text():
    output, stream = _run([
        {"type": "response.output_item.added",
         "item": {"type": "reasoning", "id": "rs_1"}},
        {"type": "response.reasoning_summary_text.delta",
         "delta": "**Formulating concise greeting**"},
        {"type": "response.reasoning_summary_part.done"},
        # Codex DONE item: encrypted payload, empty summary array.
        {"type": "response.output_item.done",
         "item": {"type": "reasoning", "id": "rs_1", "summary": [],
                  "encrypted_content": "opaque"}},
    ])
    blk = output.content[0]
    assert blk["type"] == "thinking"
    assert "Formulating concise greeting" in blk["thinking"]
    # thinking_end must carry the surviving text too (the UI collapses on it).
    end = [e for e in stream.events if e["type"] == "thinking_end"][0]
    assert "Formulating concise greeting" in end["content"]


def test_nonempty_done_summary_still_wins():
    output, _ = _run([
        {"type": "response.output_item.added",
         "item": {"type": "reasoning", "id": "rs_1"}},
        {"type": "response.reasoning_summary_text.delta", "delta": "partial"},
        {"type": "response.output_item.done",
         "item": {"type": "reasoning", "id": "rs_1",
                  "summary": [{"text": "final canonical summary"}]}},
    ])
    assert output.content[0]["thinking"] == "final canonical summary"
