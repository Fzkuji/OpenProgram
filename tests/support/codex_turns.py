"""Turns of one Codex conversation, kept as the Contexts the loop sent.

Fixtures and helpers for tests that read the requests as Codex input items
converted from the loop's ``Context`` (``convert_responses_messages`` with
the instructions as the first item). A test that compares what an adapter
actually sends uses ``tests/support/request_bytes.py`` instead.

Import the fixtures by name into a test module: ``_model_and_profile`` is
autouse there.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from openprogram.models.providers.shared.openai_responses import convert_responses_messages
from openprogram.models.providers.types import (
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    Context,
    EventDone,
    EventStart,
    Model,
    TextContent,
    ToolCall,
    Usage,
)
from openprogram.runtime.agent import turn_request as agent_turn_request
from openprogram.runtime.context.session_db import SessionDB
from openprogram.runtime.conversation import turn_runtime as D

def _model() -> Model:
    return Model(id="cache-test", name="cache-test", api="openai-codex",
                 provider="openai-codex", base_url="https://chatgpt.com/backend-api")


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: db)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: db)
    return db


@pytest.fixture(autouse=True)
def _model_and_profile(monkeypatch: pytest.MonkeyPatch):
    model = _model()
    monkeypatch.setattr(D, "_resolve_model", lambda profile, override=None: model)
    monkeypatch.setattr(D, "_load_agent_profile",
                        lambda agent_id: {"id": agent_id, "system_prompt": "you are helpful"})
    # No saved Agent named "main": the turn reads memory as an unsaved Agent
    # does, whatever an earlier test left in the registry.
    from openprogram.state.execution_facts import agent_registry
    monkeypatch.setattr(agent_registry(), "get", lambda agent_id: None)
    # The reply's identity is resolved through the registry by provider + id.
    monkeypatch.setattr("openprogram.models.providers.catalog.models.get_model",
                        lambda provider, model_id: model if model_id == model.id else None)


@pytest.fixture
def lookup_tool(monkeypatch: pytest.MonkeyPatch):
    async def execute(call_id, arguments, cancel=None, on_update=None):
        return AgentToolResult(content=[TextContent(text=f"value of {arguments.get('key')} is 42")])

    tool = AgentTool(name="lookup", label="Lookup", description="Look a key up",
                     parameters={"type": "object", "properties": {"key": {"type": "string"},
                                                                  "limit": {"type": "integer"}}},
                     execute=execute)
    from openprogram.runtime.conversation.turn_runtime import loop_runner
    monkeypatch.setattr(loop_runner, "_configure_browser_tool",
                        lambda *_args, **_kwargs: ([tool], False))
    return tool


def _scripted_stream(requests: list[Context], replies: list[AssistantMessage]):
    """Answer each request with the next scripted reply; keep every request."""
    async def stream(model, context, options):
        requests.append(Context(system_prompt=context.system_prompt,
                                messages=list(context.messages), tools=list(context.tools or [])))
        reply = replies.pop(0).model_copy(deep=True)
        reply.timestamp = int(time.time() * 1000)
        yield EventStart(partial=reply)
        yield EventDone(reason=reply.stop_reason, message=reply)
    return stream


def _reply(*blocks, stop="stop") -> AssistantMessage:
    return AssistantMessage(content=list(blocks), api="openai-codex", provider="openai-codex",
                            model="cache-test", usage=Usage(input_tokens=10, output_tokens=2),
                            stop_reason=stop, timestamp=0)


def _wire(context: Context) -> list[str]:
    """The request's input items as the Codex adapter serializes them."""
    items = convert_responses_messages(_model(), context, include_system_prompt=True)
    return [json.dumps(item, ensure_ascii=False) for item in items]


def _turn(text: str, stream, monkeypatch, session_id="c1", **request):
    original = D._run_loop_blocking

    def run(*, req, history, on_event, cancel_event, stream_fn=None, **extra):
        return original(req=req, history=history, on_event=on_event,
                        cancel_event=cancel_event, stream_fn=stream, **extra)

    monkeypatch.setattr(D, "_run_loop_blocking", run)
    result = D.process_user_turn(agent_turn_request.TurnRequest(
        session_id=session_id, user_text=text, agent_id="main", source="tui", **request))
    assert not result.failed, result.error
    return result


def _calls(*pairs) -> list[AssistantMessage]:
    """A turn that calls ``lookup`` once per round, then answers."""
    rounds = [
        _reply(ToolCall(id=f"call_{n}|fc_{n}", name="lookup", arguments=arguments), stop="toolUse")
        for n, arguments in pairs
    ]
    return rounds


def _assert_next_turn_extends(wire: list[list[str]]) -> None:
    last, first_next = wire[-2], wire[-1]
    diverged = next((index for index, (a, b) in enumerate(zip(last, first_next)) if a != b), None)
    assert diverged is None and len(first_next) > len(last), (
        f"the previous turn re-rendered differently from item {diverged}")
