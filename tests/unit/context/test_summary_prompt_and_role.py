"""The summary is written with one prompt, read as user input, and kept
only when complete (context/compaction.html, "Summary prompt and role").

- A new summary node renders as a user message with its prefix; one written
  before renders as the assistant message it was, byte for byte.
- On Anthropic the summary and the next user message go as one message.
- A reply without every section is asked once more with the reminder; still
  without them, the compaction fails, writes nothing and counts toward the
  hold.
- A summary that reaches its output cap fails.
"""
from __future__ import annotations

import asyncio

import pytest

from openprogram.models.providers.types import AssistantMessage, Model, TextContent
from openprogram.runtime.context.compaction.prompts import (
    NUDGE, SUMMARY_PROMPT, SUMMARY_SECTIONS, missing_sections,
)
from openprogram.runtime.context.persistence import SUMMARY_NODE_NAME, SUMMARY_PREFIX
from openprogram.runtime.context.render import render_dag_messages
from openprogram.state.store.session.nodes import Call, Graph
from tests.support.summary_stream import install_summary_reply

MODEL = Model(id="summary-fixture", name="fixture", api="openai-completions", provider="openai",
              base_url="https://example.invalid", context_window=200_000, max_tokens=64_000)


def sectioned(text: str = "recap") -> str:
    return "\n".join(f"## {name}\n{text}" for name in SUMMARY_SECTIONS)


def _graph(role: str | None) -> Graph:
    g = Graph()
    g.add(Call(id="u0", role="user", output="first", predecessor=None))
    g.add(Call(id="a0", role="llm", output="answer", predecessor="u0"))
    g.add(Call(id="u1", role="user", output="second", predecessor="a0"))
    metadata = {"covers_ids": ["u0", "a0"]}
    text = "[Previous conversation summary]\nold recap"
    if role:
        metadata["render_role"] = role
        text = SUMMARY_PREFIX + "new recap"
    g.add(Call(id="S", role="llm", name=SUMMARY_NODE_NAME, output=text, predecessor=None,
               metadata=metadata))
    return g


def test_a_new_summary_reads_as_user_input_with_its_prefix():
    messages = render_dag_messages(_graph("user"), ["S", "u1"])
    assert messages[0].role == "user"
    assert messages[0].content[0].text == SUMMARY_PREFIX + "new recap"


def test_an_older_summary_renders_as_it_did():
    messages = render_dag_messages(_graph(None), ["S", "u1"])
    assert messages[0].role == "assistant"
    assert messages[0].content[0].text == "[Previous conversation summary]\nold recap"


def test_on_anthropic_the_summary_and_the_next_message_go_as_one():
    from openprogram.models.providers.adapters.anthropic.anthropic import _build_messages
    from openprogram.models.providers.types import Context

    wire = _build_messages(Context(messages=render_dag_messages(_graph("user"), ["S", "u1"])))
    assert len(wire) == 1 and wire[0]["role"] == "user"
    texts = [block["text"] for block in wire[0]["content"] if block.get("type") == "text"]
    assert texts[0].startswith(SUMMARY_PREFIX) and texts[-1] == "second"


def test_the_prompt_names_every_section_and_leaves_out_what_is_sent_anyway():
    for name in SUMMARY_SECTIONS:
        assert f"## {name}" in SUMMARY_PROMPT
    assert "AGENTS.md" in SUMMARY_PROMPT
    assert "about to send" not in SUMMARY_PROMPT
    assert missing_sections(sectioned()) == []
    assert missing_sections("## User intent\nonly this") != []


def _summarise(monkeypatch, replies):
    from openprogram.runtime.context.compaction.summarize import Summarizer

    asked: list[str] = []

    async def reply(_model, context, _options):
        asked.append(context.system_prompt)
        text, stop = replies[min(len(asked) - 1, len(replies) - 1)]
        return AssistantMessage(content=[TextContent(text=text)], api=MODEL.api, provider=MODEL.provider,
                                model=MODEL.id, stop_reason=stop, timestamp=0)

    install_summary_reply(monkeypatch, reply)
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i} " + "x " * 3000,
                "_context_tokens": 4000} for i in range(12)]
    result = asyncio.run(Summarizer().summarise(messages=history, model=MODEL, context_window=20_000))
    return result, asked


def test_a_reply_without_its_sections_is_reminded_once(monkeypatch):
    result, asked = _summarise(monkeypatch, [("a loose recap", "stop"), (sectioned(), "stop")])
    assert len(asked) == 2 and NUDGE in asked[1] and NUDGE not in asked[0]
    assert result.summary_text == sectioned() and result.error is None


def test_still_without_sections_it_fails(monkeypatch):
    result, asked = _summarise(monkeypatch, [("a loose recap", "stop")])
    assert len(asked) == 2
    assert result.summary_text == "" and "missing sections" in (result.error or "")


def test_a_summary_at_its_output_cap_fails(monkeypatch):
    result, asked = _summarise(monkeypatch, [(sectioned(), "length")])
    assert len(asked) == 1
    assert result.summary_text == "" and "output cap" in (result.error or "")


def test_a_failed_summary_writes_nothing_and_counts_toward_the_hold(tmp_path, monkeypatch):
    from openprogram.runtime.context.compaction.hold import default_hold
    from openprogram.runtime.context.engine import DefaultContextEngine
    from openprogram.state.store.session.session_store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    store.create_session("c", "main")
    prev = None
    for i in range(6):
        store.append_message("c", {"id": f"u{i}", "role": "user", "content": "q " * 2000, "predecessor": prev})
        store.append_message("c", {"id": f"a{i}", "role": "assistant", "content": "r " * 2000,
                                   "predecessor": f"u{i}", "status": "done"})
        prev = f"a{i}"
    store.set_head("c", prev)
    default_hold.clear()
    failures = []
    monkeypatch.setattr(default_hold, "failed", lambda key, why, **_k: failures.append(why))

    async def loose(_model, _context, _options):
        return AssistantMessage(content=[TextContent(text="a loose recap")], api=MODEL.api,
                                provider=MODEL.provider, model=MODEL.id, timestamp=0)

    install_summary_reply(monkeypatch, loose)
    engine = DefaultContextEngine()
    monkeypatch.setattr(engine, "_occupancy_tokens", lambda *_a, **_k: 50_000)
    before = len(store.get_messages("c"))
    result = asyncio.run(engine.compact(agent=None, session_id="c", model=MODEL,
                                        keep_recent_tokens=10))
    assert not result.summary_id and len(store.get_messages("c")) == before
    assert failures and "missing sections" in failures[0]
    store.close()


@pytest.mark.parametrize("window,cap", [(200_000, 20_000), (32_000, 8_000)])
def test_the_summary_output_cap(window, cap, monkeypatch):
    from openprogram.runtime.context.compaction.bounded_summary import summarize_text
    from openprogram.models.providers.types import SimpleStreamOptions

    seen = []

    async def reply(_model, _context, options):
        seen.append(options.max_tokens)
        return AssistantMessage(content=[TextContent(text="ok")], api=MODEL.api, provider=MODEL.provider,
                                model=MODEL.id, timestamp=0)

    install_summary_reply(monkeypatch, reply)
    model = MODEL.model_copy(update={"context_window": window})
    asyncio.run(summarize_text("source", model, SimpleStreamOptions(), instruction="i", max_tokens=20_000))
    assert seen == [cap]
