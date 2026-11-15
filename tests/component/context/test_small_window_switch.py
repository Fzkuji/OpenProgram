"""Switching a conversation to a model with a small window compacts it.

A tool result is bounded once, when it is made, to the window of the model
that read it (``spill.result_cap``), and stored as that model read it.
When the conversation moves to a model whose window those results no
longer fit, the next request is made to fit by compaction, never by cutting
the stored results again: a result that is still sent goes as it was
stored.
"""
from __future__ import annotations

from openprogram.models.providers.types import AssistantMessage, TextContent, ToolResultMessage
from openprogram.runtime.context.tokens import estimate_history_tokens
from tests.support.request_bytes import call, install_bench, reply, text_tool, wire_model
from tests.support.summary_stream import install_summary_reply, sectioned

RESULT_CHARS = 31_000
CALLS = 6


def _result(arguments) -> str:
    key = str(arguments.get("key"))
    line = f"{key}: " + "z" * 70
    return "\n".join([line] * (RESULT_CHARS // (len(line) + 1)))


def test_a_smaller_window_compacts_instead_of_cutting_stored_results(monkeypatch, tmp_path):
    bench = install_bench(monkeypatch, tmp_path, "completions", [text_tool("dump", _result)])
    model = bench.model
    rounds = [call(model, n, name="dump", key=f"k{n}") for n in range(1, CALLS + 1)]
    bench.turn("dump everything", *rounds, reply(model, TextContent(text="dumped")))
    stored = {_result({"key": f"k{n}"}) for n in range(1, CALLS + 1)}

    small = wire_model("completions", "small-window").model_copy(
        update={"context_window": 32_000, "max_tokens": 4_000})
    from openprogram.runtime.conversation import turn_runtime as D
    monkeypatch.setattr(D, "_resolve_model", lambda profile, override=None: small)
    monkeypatch.setattr("openprogram.models.providers.catalog.models.get_model",
                        lambda provider, model_id: small if model_id == small.id else None)
    bench.model = small

    async def summary(_model, context, options):
        return AssistantMessage(content=[TextContent(text=sectioned("The user dumped six keys."))],
                                api=small.api, provider=small.provider, model=small.id,
                                stop_reason="stop", timestamp=0)

    install_summary_reply(monkeypatch, summary)
    bench.turn("now summarize", reply(small, TextContent(text="ok")))

    sent = bench.contexts[-1]
    assert estimate_history_tokens(sent.messages) < small.context_window
    results = [m.content[0].text for m in sent.messages if isinstance(m, ToolResultMessage)]
    # A summary stood in for the results it covered: a durable one, or,
    # when the one earlier turn is too short to cut, the request checkpoint
    # of the turn's completed rounds. Any other result goes as it was stored.
    from openprogram.runtime.context.persistence import SUMMARY_PREFIX
    texts = [m.content if isinstance(m.content, str) else getattr(m.content[0], "text", "")
             for m in sent.messages]
    assert any(t.startswith((SUMMARY_PREFIX, "[Earlier execution summary]")) for t in texts), (
        "the switch did not compact")
    assert all(text in stored for text in results), "a stored result was cut again"
