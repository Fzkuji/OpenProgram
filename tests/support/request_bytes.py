"""Real conversation turns whose requests are serialized by real adapters.

A :class:`TurnBench` runs turns through the dispatcher, the agent loop and
the DAG render with a scripted provider. Every request the loop makes is
handed to the real adapter of the bench's wire format inside
``capturing(send=False)``, so what the tests compare is the body that
adapter would send (``models/providers/recording/capture.py``), not a
conversion the test does itself.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openprogram.models.providers.recording.capture import CapturedRequest, capturing
from openprogram.models.providers.types import (
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    Context,
    EventDone,
    EventStart,
    Model,
    SimpleStreamOptions,
    TextContent,
    ToolCall,
    Usage,
)

#: Wire formats the benches run, by the adapter that writes them.
WIRES = {
    "anthropic": dict(api="anthropic-messages", provider="anthropic",
                      base_url="https://api.anthropic.com"),
    "responses": dict(api="openai-responses", provider="openai",
                      base_url="https://api.openai.com/v1"),
    "completions": dict(api="openai-completions", provider="openai",
                        base_url="https://api.openai.com/v1"),
    "codex": dict(api="openai-codex", provider="openai-codex",
                  base_url="https://chatgpt.com/backend-api"),
}


def wire_model(wire: str, model_id: str = "cache-test") -> Model:
    return Model(id=model_id, name=model_id, input=["text", "image"], context_window=200_000,
                 **WIRES[wire])


def sent(model: Model, context: Context, options: SimpleStreamOptions | None = None) -> CapturedRequest:
    """The body ``model``'s adapter sends for ``context``, stopped before the network."""
    from openprogram.models.providers.catalog.api_registry import get_api_provider

    options = (options or SimpleStreamOptions()).model_copy(
        update={"api_key": "test-key", "signal": None, "on_payload": None})

    async def drive() -> None:
        try:
            async for _event in get_api_provider(model.api).stream_simple(model, context, options):
                pass
        except Exception:  # noqa: BLE001 - RequestNotSent, or the error event it became
            pass

    with capturing(send=False) as captured:
        asyncio.run(drive()) if not _loop_running() else _run_in_thread(drive)
    assert len(captured) == 1, f"{model.api} adapter sent {len(captured)} requests"
    return captured[0]


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _run_in_thread(drive) -> None:
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    context = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(context.run, asyncio.run, drive()).result()


def reply(model: Model, *blocks, stop: str = "stop") -> AssistantMessage:
    return AssistantMessage(content=list(blocks), api=model.api, provider=model.provider,
                            model=model.id, usage=Usage(input_tokens=10, output_tokens=2),
                            stop_reason=stop, timestamp=0)


def call(model: Model, n: int, name: str = "lookup", **arguments) -> AssistantMessage:
    return reply(model, ToolCall(id=f"call_{n}|fc_{n}", name=name, arguments=arguments), stop="toolUse")


def text_tool(name: str, answer) -> AgentTool:
    """A tool answering ``answer(arguments)``: text, or an AgentToolResult."""
    async def execute(call_id, arguments, cancel=None, on_update=None):
        result = answer(arguments)
        if isinstance(result, AgentToolResult):
            return result
        return AgentToolResult(content=[TextContent(text=result)])

    return AgentTool(name=name, label=name, description=f"The {name} tool",
                     parameters={"type": "object", "properties": {
                         "key": {"type": "string"}, "limit": {"type": "integer"}}},
                     execute=execute)


def lookup_tool() -> AgentTool:
    return text_tool("lookup", lambda arguments: f"value of {arguments.get('key')} is 42")


@dataclass
class TurnBench:
    """Turns of one conversation on ``model``; ``requests`` holds every body sent."""

    model: Model
    monkeypatch: Any
    db: Any
    replies: list[AssistantMessage] = field(default_factory=list)
    requests: list[CapturedRequest] = field(default_factory=list)
    contexts: list[Context] = field(default_factory=list)

    def stream(self):
        async def scripted(model, context, options):
            self.contexts.append(Context(system_prompt=context.system_prompt,
                                         messages=list(context.messages), tools=list(context.tools or [])))
            self.requests.append(sent(self.model, context, options))
            answer = self.replies.pop(0).model_copy(deep=True)
            answer.timestamp = int(time.time() * 1000)
            yield EventStart(partial=answer)
            yield EventDone(reason=answer.stop_reason, message=answer)
        return scripted

    def turn(self, text: str, *replies: AssistantMessage, session_id: str = "c1", **request):
        from openprogram.runtime.agent import turn_request
        from openprogram.runtime.conversation import turn_runtime as D

        self.replies.extend(replies)
        original = D._run_loop_blocking
        stream = self.stream()

        def run(*, req, history, on_event, cancel_event, stream_fn=None, **extra):
            return original(req=req, history=history, on_event=on_event,
                            cancel_event=cancel_event, stream_fn=stream, **extra)

        self.monkeypatch.setattr(D, "_run_loop_blocking", run)
        result = D.process_user_turn(turn_request.TurnRequest(
            session_id=session_id, user_text=text, agent_id="main",
            **{"source": "tui", "authority_tier": "owner", **request}))
        assert not result.failed, result.error
        return result

    def history(self, index: int) -> list[str]:
        return self.requests[index].history_bytes()


def install_bench(monkeypatch, tmp_path: Path, wire: str, tools: list[AgentTool]) -> TurnBench:
    """A conversation store under ``tmp_path``, the turn's model on ``wire``,
    and ``tools`` as the turn's tools."""
    from openprogram.runtime.context.session_db import SessionDB
    from openprogram.runtime.conversation import turn_runtime as D
    from openprogram.runtime.conversation.turn_runtime import loop_runner
    from openprogram.state.execution_facts import agent_registry

    db = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: db)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: db)
    model = wire_model(wire)
    monkeypatch.setattr(D, "_resolve_model", lambda profile, override=None: model)
    monkeypatch.setattr(D, "_load_agent_profile",
                        lambda agent_id: {"id": agent_id, "system_prompt": "you are helpful"})
    # No saved Agent named "main": the turn reads memory as an unsaved Agent does.
    monkeypatch.setattr(agent_registry(), "get", lambda agent_id: None)
    monkeypatch.setattr("openprogram.models.providers.catalog.models.get_model",
                        lambda provider, model_id: model if model_id == model.id else None)
    monkeypatch.setattr(loop_runner, "_configure_browser_tool",
                        lambda *_args, **_kwargs: (list(tools), False))
    return TurnBench(model=model, monkeypatch=monkeypatch, db=db)


def first_difference(earlier: list[str], later: list[str]) -> int | None:
    """Where ``later`` stops extending ``earlier``; None when it extends it."""
    for index, (a, b) in enumerate(zip(earlier, later)):
        if a != b:
            return index
    return None if len(later) >= len(earlier) else len(later)


def assert_extends(earlier: list[str], later: list[str]) -> None:
    at = first_difference(earlier, later)
    assert at is None, (
        f"re-rendered differently from item {at}:\nsent   {earlier[at][:300] if at < len(earlier) else '-'}"
        f"\nre-sent {later[at][:300] if at < len(later) else '-'}")


def items(request: CapturedRequest) -> list[dict]:
    return [json.loads(text) for text in request.history_bytes()]
