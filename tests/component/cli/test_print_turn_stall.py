"""A ``--print`` turn whose first reply stalls sends the request again.

The turn path that ``openprogram --print`` and the Worker run calls the agent
loop directly, with no ``AgentSession`` around it. A fake API provider stalls
its first reply before any event, after reasoning, or after text; with the
idle bound shortened, the request goes out a second time, the turn completes
with the second reply only, and the usage file counts both requests.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from openprogram.interfaces.cli import chat as chat_module
from openprogram.models.providers.catalog import api_registry
from openprogram.models.providers.types import (
    AssistantMessage,
    EventDone,
    EventStart,
    EventTextDelta,
    EventTextStart,
    EventThinkingDelta,
    EventThinkingStart,
    TextContent,
    ThinkingContent,
    Usage,
)
from openprogram.models.providers.utils import stream_retry
from tests.component.cli.test_print_turn import (  # noqa: F401 - fixtures
    API,
    MODEL,
    _never_in_a_real_profile,
    provider,
)


class Stalls:
    """Stalls every reply of the turn up to ``stalls`` times, then answers."""

    def __init__(self, first: str, stalls: int = 1) -> None:
        self.first, self.stalls, self.calls = first, stalls, 0

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        def message(content=()):
            return AssistantMessage(
                content=list(content), api=model.api, provider=model.provider, model=model.id,
                stop_reason="stop", timestamp=0, usage=Usage(input=10, output=1, total_tokens=11))

        if not context.tools:  # a call beside the turn (its title)
            title = message([TextContent(text="Title")])
            yield EventStart(partial=title)
            yield EventDone(reason="stop", message=title)
            return
        self.calls += 1
        partial = AssistantMessage(content=[], api=model.api, provider=model.provider,
                                   model=model.id, stop_reason="stop", timestamp=0)
        yield EventStart(partial=partial)
        if self.calls <= self.stalls:
            if self.first == "thinking":
                partial.content = [ThinkingContent(thinking="Inspecting files")]
                yield EventThinkingStart(content_index=0, partial=partial)
                yield EventThinkingDelta(content_index=0, delta="Inspecting files", partial=partial)
            elif self.first == "text":
                partial.content = [TextContent(text="I will")]
                yield EventTextStart(content_index=0, partial=partial)
                yield EventTextDelta(content_index=0, delta="I will", partial=partial)
            await asyncio.Event().wait()
        yield EventDone(reason="stop", message=message([TextContent(text="done")]))


@pytest.fixture
def stalling(provider, monkeypatch):
    def install(first: str, stalls: int = 1) -> Stalls:
        monkeypatch.setenv("OPENPROGRAM_STREAM_IDLE_TIMEOUT_S", "0.3")
        monkeypatch.setattr(stream_retry, "_BACKOFF_BASE_S", 0.01)
        provider(0)
        fake = Stalls(first, stalls)
        monkeypatch.setitem(api_registry._registry, API, fake)
        return fake

    return install


def _run(tmp_path) -> tuple[int, dict]:
    usage = tmp_path / "usage.json"
    code = 0
    try:
        chat_module.run_cli_chat(oneshot="go", tui=False, model=MODEL, usage_file=str(usage))
    except SystemExit as exc:
        code = exc.code or 0
    return code, json.loads(usage.read_text(encoding="utf-8"))


@pytest.mark.parametrize("first", ["start", "thinking", "text"])
def test_a_turn_whose_first_reply_stalls_is_sent_again_and_completes(first, stalling, tmp_path, capsys):
    fake = stalling(first)

    code, summary = _run(tmp_path)

    out = capsys.readouterr()
    assert code == 0
    assert fake.calls == 2
    assert summary["ended"] == "completed" and summary["error"] is None
    # Only the reply that got through is the answer: nothing of the stalled one.
    assert out.out == "done\n"
    # Both requests are in the usage: the stalled one with its cost unknown.
    assert summary["model_requests"] == 2
    assert summary["unpriced_requests"] == 1
    assert summary["cost_usd"] is None
    assert (summary["input_tokens"], summary["output_tokens"]) == (10, 1)
    assert summary["agent_rounds"] == 1


def test_a_reply_that_keeps_stalling_fails_the_turn_within_the_bound(stalling, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(stream_retry, "PROVIDER_STREAM_MAX_ATTEMPTS", 3)
    fake = stalling("text", stalls=99)

    code, summary = _run(tmp_path)

    assert code != 0
    assert fake.calls == 3
    assert summary["ended"] == "failed" and "stream event" in summary["error"]
    # Every request that stalled is counted, none of them priced.
    assert summary["model_requests"] == 3
    assert summary["unpriced_requests"] == 3


def test_a_resent_request_counts_in_the_turns_provider_requests():
    from types import SimpleNamespace

    from openprogram.models.providers.types import EventRetry
    from openprogram.runtime.conversation.turn_runtime import loop_runner

    tally = loop_runner._TurnTally()

    def update(event):
        loop_runner._record_agent_event(
            SimpleNamespace(type="message_update", assistant_message_event=event),
            tally, req=None, on_event=lambda _: None, ordered_blocks_out=None)

    update(EventRetry(attempt=2, max_attempts=5, reason="transport", restart=True))
    update(EventRetry(attempt=2, reason="output_limit"))  # its reply's turn_end counts it

    assert tally.usage_total["provider_request_count"] == 1
