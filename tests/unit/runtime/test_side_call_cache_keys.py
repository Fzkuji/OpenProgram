"""Side calls that repeat a prefix reach the Codex cache that holds it.

A title or a topic sort makes a new Runtime per call. Measured on the
ChatGPT backend, each of those calls used to carry its Runtime's own random
id as ``prompt_cache_key`` and read 0 cached tokens out of about 3,000,
although every call of a kind sends the same instructions; with one key per
prefix the second call read the whole shared prefix.
"""
from __future__ import annotations

import json
import time

import openprogram.models.providers.adapters.openai_codex.openai_codex as cx
from openprogram.models.providers.shared.openai_responses import (
    convert_responses_messages,
)
from openprogram.models.providers.types import (
    AssistantMessage,
    EventDone,
    EventStart,
    TextContent,
)
from openprogram.runtime.agent.runtime import Runtime


class _CodexModel:
    id = "gpt-test"
    api = "openai-codex"
    provider = "openai-codex"
    reasoning = False
    input = ["text"]


def _capturing_stream_fn(seen: list):
    def stream_fn(model, context, opts):
        seen.append((context, opts))
        msg = AssistantMessage(
            content=[TextContent(text="ok")], api="openai-codex", provider="openai-codex",
            model="gpt-test", stop_reason="stop", timestamp=int(time.time() * 1000),
        )

        async def gen():
            yield EventStart(partial=msg)
            yield EventDone(reason="stop", message=msg)

        return gen()

    return stream_fn


def _codex_body(context, opts) -> dict:
    model = _CodexModel()
    session_id = getattr(opts, "session_id", None)
    messages = convert_responses_messages(model, context, include_system_prompt=False)
    return cx._build_request_body(model, context, {"session_id": session_id}, messages)


def _one_call(system: str, text: str) -> dict:
    """What a title or topic sort does: a new Runtime, its system prompt, one exec."""
    seen: list = []
    runtime = Runtime(call=lambda *_a, **_k: "unused")
    runtime.system = system
    runtime.exec(content=[{"type": "text", "text": text}], toolset="none",
                 stream_fn=_capturing_stream_fn(seen))
    assert seen, "no provider request was made"
    return _codex_body(*seen[0])


def _cached_prefix(body: dict, text: str) -> str:
    """The request in the order the model reads it — instructions, tools,
    then input — cut where the caller's own text starts."""
    wire = json.dumps(
        {"instructions": body["instructions"], "tools": body.get("tools"),
         "input": body["input"]},
        ensure_ascii=False,
    )
    cut = wire.index(text)
    assert cut > wire.index('"input"'), "the variable text sits before the input"
    return wire[:cut]


def test_new_runtimes_with_one_prompt_share_the_key_and_the_prefix():
    first = _one_call("Name this conversation.", "<session>rotate a PDF page</session>")
    second = _one_call("Name this conversation.", "<session>a jazz evening in Lisbon</session>")
    assert first["prompt_cache_key"] == second["prompt_cache_key"]
    assert first["prompt_cache_key"].startswith("op-")
    assert "Name this conversation." in first["instructions"]
    assert first["instructions"] == second["instructions"]
    assert first.get("tools") == second.get("tools")
    # Everything the model reads before the user's own text is byte-identical.
    assert (_cached_prefix(first, "rotate a PDF page")
            == _cached_prefix(second, "a jazz evening in Lisbon"))


def test_the_prefix_comparison_sees_the_instructions():
    # Guards the check above: a different system prompt must change the prefix.
    first = _one_call("Name this conversation.", "<session>qx-7731</session>")
    second = _one_call("A completely different prompt.", "<session>qx-7731</session>")
    assert _cached_prefix(first, "qx-7731") != _cached_prefix(second, "qx-7731")


def test_a_different_prompt_gets_a_route_of_its_own():
    title = _one_call("Name this conversation.", "<session>x</session>")
    topics = _one_call("Sort these messages into topics.", "<messages>x</messages>")
    assert title["prompt_cache_key"] != topics["prompt_cache_key"]


def test_a_session_id_the_caller_set_is_used_as_given():
    seen: list = []
    runtime = Runtime(call=lambda *_a, **_k: "unused")
    runtime.session_id = "local_conversation"
    runtime.exec(content=[{"type": "text", "text": "hi"}], toolset="none",
                 stream_fn=_capturing_stream_fn(seen))
    assert _codex_body(*seen[0])["prompt_cache_key"] == "local_conversation"


def test_a_side_call_keeps_its_instructions_across_midnight_and_is_told_the_date(monkeypatch):
    """The date is not in the instructions, which come first in a Codex
    request, so a call made the next day reads the same cached prefix; the
    model is told the date beside the call's own message."""
    import datetime

    from openprogram.runtime.context import components

    day = {"value": datetime.date(2026, 11, 12)}
    monkeypatch.setattr(components, "current_date_note",
                        lambda today=None: day["value"].strftime("Today is %A, %B %d, %Y."))

    def model_call(text):
        seen: list = []
        runtime = Runtime(call=lambda *_a, **_k: "unused")
        runtime._call_fn = None  # a model the runtime calls, as a title or topic sort has
        runtime.system = "Name this conversation."
        runtime.exec(content=[{"type": "text", "text": text}], toolset="none",
                     stream_fn=_capturing_stream_fn(seen))
        return _codex_body(*seen[0])

    first = model_call("<session>rotate a PDF page</session>")
    day["value"] = datetime.date(2026, 11, 13)
    second = model_call("<session>rotate a PDF page</session>")
    assert first["instructions"] == second["instructions"]
    assert "Today is" not in first["instructions"]
    assert "Today is Thursday, November 12, 2026." in json.dumps(first["input"])
    assert "Today is Friday, November 13, 2026." in json.dumps(second["input"])
    assert _cached_prefix(first, "Today is") == _cached_prefix(second, "Today is")


def test_a_call_made_straight_to_the_adapter_gets_the_runtimes_key():
    """A summary or a permission check calls the adapter without a Runtime;
    with the same model, instructions and tools it is keyed as a Runtime
    call is (``providers.shared.cache_key``), not by a second derivation."""
    from openprogram.models.providers.shared.cache_key import prefix_cache_key
    from openprogram.models.providers.types import Context

    through_runtime = _one_call("Name this conversation.", "<session>rotate a PDF page</session>")
    context = Context(system_prompt=through_runtime["instructions"], messages=[])
    direct = cx._build_request_body(_CodexModel(), context, {}, [])
    assert direct["prompt_cache_key"] == prefix_cache_key(
        "openai-codex", "gpt-test", through_runtime["instructions"], [])
