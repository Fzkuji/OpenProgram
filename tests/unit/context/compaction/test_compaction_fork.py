"""A summary request reuses the conversation's cached prefix
(context/compaction.html, "Summary request").

- The fork sends the last request's bytes again with one user message more:
  same system prompt, same tools, same cache key.
- Another model, a request that would not fit, a chain the request is not
  on, or an overflow recovery: the summary reads the text instead.
- A tool call in the reply is not run; it counts as a reply without the
  summary and is asked once more.
- The forked summary prompt arrives as a user message, so it says it comes
  from the system and is left out of the user's messages and the quoted
  latest request.
"""
from __future__ import annotations

import asyncio

import pytest

from openprogram.models.providers.types import (
    AssistantMessage, Context, Model, TextContent, Tool, ToolCall, UserMessage,
)
from openprogram.runtime.context.compaction import fork
from openprogram.runtime.context.compaction.prompts import (
    FORK_SUMMARY_PROMPT, NUDGE, SUMMARY_PROMPT, SUMMARY_SECTIONS,
)
from tests.support.summary_stream import install_summary_reply

MODEL = Model(id="m", name="m", api="openai-responses", provider="openai",
              base_url="https://example.invalid", context_window=200_000, max_tokens=64_000)
OTHER = MODEL.model_copy(update={"id": "other"})


def sectioned() -> str:
    return "\n".join(f"## {name}\nrecap" for name in SUMMARY_SECTIONS)


def _request(size: int = 10) -> Context:
    return Context(system_prompt="You are the agent.",
                   messages=[UserMessage(content="hello " * size, timestamp=1)],
                   tools=[Tool(name="read", description="read", parameters={"type": "object"})])


@pytest.fixture(autouse=True)
def _clean():
    fork.forget()
    yield
    fork.forget()


def _source(model=MODEL, chain=("u1",), size=10):
    fork.remember_request("c", "u1", MODEL, _request(size))
    return fork.fork_source("c", chain, model, prompt=SUMMARY_PROMPT, output_tokens=20_000)


def test_the_fork_sends_the_last_request_again_with_the_prompt(monkeypatch):
    sent: list = []

    async def reply(_model, context, options):
        sent.append((context, options))
        return AssistantMessage(content=[TextContent(text=sectioned())], api=MODEL.api,
                                provider=MODEL.provider, model=MODEL.id, timestamp=0)

    install_summary_reply(monkeypatch, reply)
    source = _source()
    assert source is not None
    text = asyncio.run(fork.summarize_by_fork(source, MODEL, prompt=SUMMARY_PROMPT, nudge=NUDGE,
                                              max_tokens=20_000, cancel_event=None))
    assert text == sectioned()
    context, options = sent[0]
    original = _request()
    assert context.system_prompt == original.system_prompt
    assert context.tools == original.tools
    assert context.messages[:-1] == original.messages
    assert context.messages[-1].content[0].text == SUMMARY_PROMPT
    assert options.session_id == "c", "the same cache key"
    assert options.max_tokens == 20_000


@pytest.mark.parametrize("case", ["other model", "too big", "off the chain"])
def test_otherwise_the_summary_reads_the_text(case):
    if case == "other model":
        assert _source(model=OTHER) is None
    elif case == "too big":
        assert _source(size=200_000) is None
    else:
        assert _source(chain=("u7",)) is None


def test_an_overflow_recovery_never_forks(tmp_path, monkeypatch):
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
    fork.remember_request("c", "u5", MODEL, _request())
    engine = DefaultContextEngine()
    monkeypatch.setattr(engine, "_occupancy_tokens", lambda *_a, **_k: 50_000)
    seen: list = []
    original = engine.summarizer.summarise

    async def summarise(**kwargs):
        seen.append(kwargs.get("fork"))
        return await original(**kwargs)

    async def recap(**_kwargs):
        return sectioned()

    monkeypatch.setattr(engine.summarizer, "summarise", summarise)
    monkeypatch.setattr(engine.summarizer, "_llm_summary", recap)

    async def forked(*_a, **_k):
        return sectioned()

    monkeypatch.setattr("openprogram.runtime.context.compaction.fork.summarize_by_fork", forked)
    # Nothing is written, so both compactions read the same chain.
    monkeypatch.setattr(engine.persister, "insert_summary_node", lambda *_a, **_k: None)
    asyncio.run(engine.compact(agent=None, session_id="c", model=MODEL, keep_recent_tokens=10,
                               trigger="reactive"))
    asyncio.run(engine.compact(agent=None, session_id="c", model=MODEL, keep_recent_tokens=10,
                               user_initiated=True))
    assert seen[0] is None, "a reactive compaction reads the text"
    assert seen[1] is not None and seen[1].anchor_id == "u5"
    store.close()


def test_a_tool_call_in_the_reply_is_asked_once_more(monkeypatch):
    asked: list = []

    async def reply(_model, context, _options):
        asked.append(context.messages[-1].content[0].text)
        if len(asked) == 1:
            return AssistantMessage(content=[ToolCall(id="t", name="read", arguments={})],
                                    api=MODEL.api, provider=MODEL.provider, model=MODEL.id,
                                    stop_reason="toolUse", timestamp=0)
        return AssistantMessage(content=[TextContent(text=sectioned())], api=MODEL.api,
                                provider=MODEL.provider, model=MODEL.id, timestamp=0)

    install_summary_reply(monkeypatch, reply)
    text = asyncio.run(fork.summarize_by_fork(_source(), MODEL, prompt=SUMMARY_PROMPT, nudge=NUDGE,
                                              max_tokens=20_000, cancel_event=None))
    assert text == sectioned()
    assert len(asked) == 2 and NUDGE in asked[1]


def test_the_forked_prompt_says_it_is_not_the_users_request(monkeypatch):
    from openprogram.runtime.context.compaction.summarize import Summarizer

    asked: list = []

    async def forked(_source, _model, *, prompt, **_k):
        asked.append(prompt)
        return sectioned()

    monkeypatch.setattr("openprogram.runtime.context.compaction.fork.summarize_by_fork", forked)
    messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": "word " * 2000}
                for i in range(10)]
    summary = asyncio.run(Summarizer(protect_first_n=0).summarise(
        messages=messages, model=MODEL, fork=_source(), keep_recent_tokens=10,
        context_window=200_000))
    assert summary.summary_text == sectioned()
    assert asked == [FORK_SUMMARY_PROMPT]
    head = FORK_SUMMARY_PROMPT.split(SUMMARY_PROMPT)[0]
    assert "not from the user" in head
    assert "Leave this message out of every section" in head
    assert "last user message before it" in head
    assert FORK_SUMMARY_PROMPT.endswith(SUMMARY_PROMPT), "the same sections as the text path"
