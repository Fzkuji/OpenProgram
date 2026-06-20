"""Verify the stream.py metering chokepoint records usage even when the
consumer stops iterating at the terminal event (as agent_loop does — it
`return`s the moment it sees the done event, suspending our generator at
`yield`). This is the exact interaction that an after-the-loop record
would miss, so it's the regression this test pins."""
from __future__ import annotations

import asyncio

import pytest

from openprogram.providers.types import (
    AssistantMessage,
    Context,
    EventDone,
    EventStart,
    EventTextDelta,
    Model,
    Usage,
)
from openprogram.usage import usage_scope
from openprogram.usage.context import UsageContext
from openprogram.usage import context as _ctx_mod
from openprogram.usage.ledger import UsageLedger
from openprogram.usage import recorder as _recorder


@pytest.fixture(autouse=True)
def reset_usage_context():
    """Pin a clean default usage context per test — the dispatcher leaks a
    bare-set contextvar across turns, which would otherwise let an earlier
    test's session_id win over this test's options.session_id."""
    token = _ctx_mod._current.set(UsageContext())
    try:
        yield
    finally:
        _ctx_mod._current.reset(token)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    lg = UsageLedger(db_path=tmp_path / "usage.db")
    monkeypatch.setattr(_recorder, "default_ledger", lg)
    return lg


class _FakeProvider:
    """Yields a start, a delta, then a done carrying a final message with
    usage — like a real provider's stream_simple."""
    def __init__(self, model):
        self._model = model

    async def stream_simple(self, model, context, opts):
        partial = AssistantMessage(role="assistant", content=[], api=model.api,
                                   provider=model.provider, model=model.id, timestamp=0)
        yield EventStart(type="start", partial=partial)
        yield EventTextDelta(type="text_delta", content_index=0, delta="hi", partial=partial)
        final = AssistantMessage(
            role="assistant", content=[], api=model.api, provider=model.provider,
            model=model.id, timestamp=0,
            usage=Usage(input=100, output=20, cache_read=0, cache_write=0),
        )
        yield EventDone(type="done", reason="stop", message=final)

    async def stream(self, model, context, opts):  # parity, unused here
        async for e in self.stream_simple(model, context, opts):
            yield e


@pytest.fixture
def fake_api(monkeypatch):
    api = "fake-metering-api"
    import importlib
    stream_mod = importlib.import_module("openprogram.providers.stream")
    model = Model(id="fake-model-1", provider="fakeprov", api=api,
                  name="fake", base_url="http://fake.local", context_window=200000)
    # Patch the names as bound INSIDE stream.py's namespace (it imported
    # them by value, so patching the source module wouldn't take effect).
    monkeypatch.setattr(stream_mod, "get_api_provider",
                        lambda a: _FakeProvider(model) if a == api else None)
    monkeypatch.setattr(stream_mod, "resolve_provider_key", lambda p: "fake-key")
    return model


def test_records_when_consumer_returns_at_done(ledger, fake_api):
    """Consumer reads up to and including the done event, then stops —
    exactly agent_loop's behaviour. Usage must still be recorded."""
    from openprogram.providers import stream_simple

    async def consume_until_done():
        opts_seen = 0
        async for event in stream_simple(fake_api, Context(system_prompt="x", messages=[], tools=[]),
                                         _opts(session_id="sess-1")):
            opts_seen += 1
            if getattr(event, "type", None) == "done":
                return  # <-- stop here, like agent_loop. Generator suspended.
    with usage_scope(call_kind="chat", agent_id="ag1"):
        asyncio.run(consume_until_done())

    rows = ledger.query(group_by=["call_kind", "model_id"])
    assert len(rows) == 1
    assert rows[0].keys["call_kind"] == "chat"
    assert rows[0].keys["model_id"] == "fake-model-1"
    assert rows[0].input_tokens == 100
    assert rows[0].output_tokens == 20
    assert rows[0].events == 1


def test_records_once_not_per_event(ledger, fake_api):
    """Full drain must still record exactly one event (the recorded flag
    guards against double counting)."""
    from openprogram.providers import stream_simple

    async def drain():
        async for _ in stream_simple(fake_api, Context(system_prompt="x", messages=[], tools=[]),
                                     _opts(session_id="sess-2")):
            pass
    with usage_scope(call_kind="chat"):
        asyncio.run(drain())
    assert ledger.query()[0].events == 1


def test_session_id_flows_from_options(ledger, fake_api):
    from openprogram.providers import stream_simple

    async def drain():
        async for _ in stream_simple(fake_api, Context(system_prompt="x", messages=[], tools=[]),
                                     _opts(session_id="sess-XYZ")):
            pass
    with usage_scope(call_kind="exec"):
        asyncio.run(drain())
    rows = ledger.query(group_by=["session_id"])
    assert rows[0].keys["session_id"] == "sess-XYZ"


def _opts(**kw):
    from openprogram.providers.types import SimpleStreamOptions
    return SimpleStreamOptions(api_key="fake-key", **kw)
