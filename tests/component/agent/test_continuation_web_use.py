"""Resume binds turn identity so web_use tokens work and live cards persist."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openprogram.agent import dispatcher as D
from openprogram.agent.continuation import runtime_contract_snapshot
from openprogram.agent.dispatcher.types import TurnRequest
from openprogram.agent.session_db import SessionDB
from openprogram.context.nodes import ROLE_CODE, ROLE_LLM, ROLE_USER, Call
from openprogram.providers.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
)
from openprogram.store import SessionNodeWriter


SESSION_ID = "local_continuation_web_use"
USER_ID = "user-1"
ASSISTANT_ID = "user-1_reply"
EXPECTED_OWNER = f"turn:{SESSION_ID}:{ASSISTANT_ID}"


class _PageAdapter:
    supports_operation_guard = True

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, str]] = []

    def observe(self, session, arguments, *, before_dispatch=None):
        del arguments
        if before_dispatch is not None:
            before_dispatch()
        self.calls.append(("observe", session.owner_id))
        return {
            "frame_id": "frame_1_resume",
            "url": "http://127.0.0.1:62147/page/1?acceptance=polish",
            "text": "Counter: 4\nDocument instance: afbd91da-fe30-4cf5-953b-0d46a588c177",
        }

    def act(self, session, arguments, *, before_dispatch=None):
        del arguments
        if before_dispatch is not None:
            before_dispatch()
        self.calls.append(("act", session.owner_id))
        return {"ok": True, "detail": "clicked e2"}

    def verify(self, session, arguments, *, before_dispatch=None):
        del arguments
        if before_dispatch is not None:
            before_dispatch()
        self.calls.append(("verify", session.owner_id))
        return {"passed": True}

    def close(self, session):
        self.calls.append(("close", session.owner_id))


def _inventory(_context=None):
    return {
        "context_id": "page_ctx_capture",
        "window_id": "main",
        "surfaces": [{
            "binding_id": "binding-p1",
            "page_key": "page:p1",
            "surface_key": "p1",
            "window_id": "main",
            "tab_id": "tab-p1",
            "aliases": ["p1"],
            "capabilities": ["observe", "interact", "navigate"],
            "title": "Resource test 1",
            "origin": "http://127.0.0.1:62147",
            "visible": True,
            "focused": True,
        }],
    }


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr(
        "openprogram.store.session.session_store.default_store", lambda: db,
    )
    monkeypatch.setattr("openprogram.store.default_store", lambda: db)
    return db


@pytest.fixture
def web_use_host(monkeypatch: pytest.MonkeyPatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow.browser import web_use_runtime
    from openprogram.programs.workflow.browser.web_use_runtime import (
        SUPPORTED_BACKENDS,
        WebUseSessionRegistry,
    )

    adapters = {name: _PageAdapter(name) for name in SUPPORTED_BACKENDS}
    registry = WebUseSessionRegistry(
        adapters=adapters,
        binding_validator=lambda _binding_id: {"ok": True},
        page_key_resolver=lambda binding_id: binding_id,
        binding_revision_resolver=lambda _binding_id: {
            "page_revision": 1, "access_revision": 1, "geometry_revision": 1,
        },
        release_context=lambda _context: None,
    )
    monkeypatch.setattr(web_use_runtime, "_registry", registry)
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "capture_pages", _inventory)
    monkeypatch.setattr(surface_context, "capture_active", _inventory)
    monkeypatch.setattr(
        "openprogram.browser_resources.writes_fenced", lambda _page: False,
    )
    monkeypatch.setattr(
        "openprogram.browser_resources.attribute_operating_page",
        lambda _page: None,
    )
    yield registry, adapters
    registry.close_all()


def _seed_session(db: SessionDB) -> None:
    db.create_session(SESSION_ID, "main", source="component")
    writer = SessionNodeWriter(db, SESSION_ID)
    writer.append(Call(
        id=USER_ID, created_at=1.0, role=ROLE_USER, output="continue pages",
        metadata={"source": "component"},
    ))
    writer.append(Call(
        id=ASSISTANT_ID, created_at=1.1, role=ROLE_LLM, output="",
        predecessor=USER_ID,
        metadata={"status": "running", "source": "component"},
    ))
    writer.append(Call(
        id=f"{ASSISTANT_ID}_t_call-pre",
        created_at=1.2,
        role=ROLE_CODE,
        name="web_use",
        input={"command": "observe"},
        output="Counter: 4",
        caller=ASSISTANT_ID,
        metadata={"tool_call_id": "call-pre", "is_error": False},
    ))


def _continuation():
    request = TurnRequest(
        session_id=SESSION_ID,
        user_text="continue pages",
        agent_id="main",
        source="component",
        user_msg_id=USER_ID,
        user_already_persisted=True,
    )
    request._execution_revision_id = "rev-continuation"
    decision = AssistantMessage(
        content=[
            TextContent(text="Counter is 4"),
            ToolCall(
                id="call-pre",
                name="web_use",
                arguments={"command": "observe"},
            ),
        ],
        api="fake",
        provider="fake",
        model="fake",
        timestamp=1,
        stop_reason="toolUse",
    )
    tool_results = (
        ToolResultMessage(
            tool_call_id="call-pre",
            tool_name="web_use",
            content=[TextContent(text="Counter: 4")],
            timestamp=1,
        ),
    )
    snapshot = runtime_contract_snapshot(
        model=SimpleNamespace(
            id="fake", name="fake", api="fake", provider="fake",
        ),
        system_prompt="",
        tools=[],
        request=request,
    )
    state = SimpleNamespace(payload={
        "turn": {
            "user_message_id": USER_ID,
            "assistant_message_id": ASSISTANT_ID,
            "base_history_head_id": USER_ID,
        },
        "safe_point": {"phase": "after_provider"},
    })
    return SimpleNamespace(
        request=request,
        checkpoint=SimpleNamespace(),
        state=state,
        assistant_message=decision,
        tool_results=tool_results,
        resolved_snapshot=snapshot,
        assistant_message_id=ASSISTANT_ID,
    )


def _emit_tool(on_event, *, tool_call_id: str, command: str, result) -> None:
    on_event({
        "type": "chat_response",
        "data": {
            "type": "stream_event",
            "event": {
                "type": "tool_use",
                "tool": "web_use",
                "tool_call_id": tool_call_id,
                "input": {"command": command},
            },
        },
    })
    on_event({
        "type": "chat_response",
        "data": {
            "type": "stream_event",
            "event": {
                "type": "tool_result",
                "tool": "web_use",
                "tool_call_id": tool_call_id,
                "result": result,
                "is_error": False,
            },
        },
    })
    on_event({
        "type": "chat_response",
        "data": {
            "type": "stream_event",
            "event": {"type": "text", "text": f"{command} ok"},
        },
    })


def _run_web_use_round(label: str) -> dict:
    from openprogram.agent.surface_context import web_use_owner_id
    from openprogram.programs.workflow.browser import web_use

    from openprogram.programs.workflow.browser.web_use_runtime import get_registry

    owner = web_use_owner_id()
    listed = web_use(command="list_pages", backend="open_claude_chrome")
    token = listed["pages"][0]["page_context_token"]
    foreign = get_registry().execute(
        command="observe",
        backend="open_claude_chrome",
        owner_id="turn:other-session:user-1_reply",
        page_context_token=token,
    )
    assert foreign.get("reason_code") == "page_context_owner_mismatch"
    observed = web_use(
        command="observe",
        backend="open_claude_chrome",
        page="p1",
        page_context_token=token,
    )
    assert observed.get("reason_code") is None
    assert observed["frame_id"] == "frame_1_resume"
    acted = web_use(
        command="act",
        backend="open_claude_chrome",
        web_session_id=observed["web_session_id"],
        arguments={"action": "click", "expected_frame_id": observed["frame_id"]},
    )
    assert acted.get("ok") is True
    return {
        "label": label,
        "owner": owner,
        "token": token,
        "session": observed["web_session_id"],
        "list_ok": listed.get("ok") is True,
    }


def test_continuation_reuses_turn_owner_and_preserves_live_cards(
    tmp_db: SessionDB, web_use_host, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, adapters = web_use_host
    _seed_session(tmp_db)
    continuation = _continuation()
    captured = {"owners": [], "events": []}
    rounds = []

    def _loop(*, req, history, on_event, cancel_event, ordered_blocks_out=None,
              execution_context=None, continuation=None, **_extra):
        del req, history, cancel_event
        round_label = "resume-1" if not rounds else "resume-2"
        result = _run_web_use_round(round_label)
        captured["owners"].append(result["owner"])
        rounds.append(result)
        new_id = f"call-{round_label}"
        if round_label == "resume-2":
            _emit_tool(
                on_event, tool_call_id="call-pre", command="observe",
                result="Counter: 4",
            )
        _emit_tool(
            on_event, tool_call_id=new_id, command="observe",
            result=result["session"],
        )
        if ordered_blocks_out is not None:
            if not ordered_blocks_out:
                ordered_blocks_out.extend([
                    {"type": "text", "text": "Counter is 4"},
                    {
                        "type": "tool",
                        "tool": "web_use",
                        "tool_call_id": "call-pre",
                        "input": json.dumps({"command": "observe"}),
                    },
                ])
            ordered_blocks_out.append({
                "type": "text", "text": f"{round_label} restored",
            })
            ordered_blocks_out.append({
                "type": "tool",
                "tool": "web_use",
                "tool_call_id": new_id,
                "input": json.dumps({"command": "observe"}),
            })
        tool_calls = [
            {
                "id": "call-pre", "tool_call_id": "call-pre",
                "tool": "web_use", "result": "Counter: 4", "is_error": False,
            },
            {
                "id": new_id, "tool_call_id": new_id,
                "tool": "web_use", "result": result["session"], "is_error": False,
            },
        ]
        if execution_context is not None and round_label == "resume-1":
            execution_context["safe_point_committed"] = True
        return f"{round_label} restored", {"input_tokens": 1, "output_tokens": 1}, tool_calls

    def _collect(env):
        captured["events"].append(env)

    with patch.object(D, "_run_loop_blocking", _loop):
        first = D.process_agent_continuation(
            continuation, on_event=_collect, execution_context={},
        )
        assert getattr(first, "_execution_safe_point_handoff", False) is True

    assert captured["owners"] == [EXPECTED_OWNER]
    assert tmp_db.message_exists(SESSION_ID, f"{ASSISTANT_ID}_t_call-pre")
    assert tmp_db.message_exists(SESSION_ID, f"{ASSISTANT_ID}_t_call-resume-1")
    assistant = next(row for row in tmp_db.get_messages(SESSION_ID) if row["id"] == ASSISTANT_ID)
    extra = assistant.get("extra") or (assistant.get("metadata") or {}).get("extra")
    assert not extra or "call-resume-1" not in str(extra)
    assert not any(
        event.get("type") == "chat_ack" for event in captured["events"]
    )
    denied = registry.execute(
        command="observe",
        backend="open_claude_chrome",
        owner_id="turn:other-session:user-1_reply",
        page_context_token=rounds[0]["token"],
    )
    assert denied.get("reason_code") in {
        "page_context_owner_mismatch", "page_context_not_found",
    }

    captured["events"].clear()
    with patch.object(D, "_run_loop_blocking", _loop):
        second = D.process_agent_continuation(
            continuation, on_event=_collect, execution_context={},
        )
    assert second.failed is False
    assert captured["owners"] == [EXPECTED_OWNER, EXPECTED_OWNER]
    assert tmp_db.message_exists(SESSION_ID, f"{ASSISTANT_ID}_t_call-resume-2")
    # Replaying the already-persisted pre-pause card must not duplicate it.
    pre_nodes = [
        row for row in tmp_db.get_messages(SESSION_ID)
        if row.get("id") == f"{ASSISTANT_ID}_t_call-pre"
    ]
    assert len(pre_nodes) == 1
    assistant = next(row for row in tmp_db.get_messages(SESSION_ID) if row["id"] == ASSISTANT_ID)
    payload = assistant.get("extra") or (assistant.get("metadata") or {}).get("extra")
    if isinstance(payload, str):
        payload = json.loads(payload)
    blocks = payload["blocks"]
    kinds = [(block.get("type"), block.get("tool_call_id") or block.get("text")) for block in blocks]
    assert kinds[0][0] == "text" and kinds[0][1] == "Counter is 4"
    assert ("tool", "call-pre") in kinds
    assert ("tool", "call-resume-2") in kinds
    assert adapters["open_claude_chrome"].calls
    assert {owner for _cmd, owner in adapters["open_claude_chrome"].calls} == {EXPECTED_OWNER}

    leftover = registry.execute(
        command="observe",
        backend="open_claude_chrome",
        owner_id=EXPECTED_OWNER,
        page_context_token=rounds[1]["token"],
    )
    assert leftover.get("reason_code") in {
        "page_context_not_found", "page_context_consumed", "owner_closing",
        "web_session_not_found",
    }


def _display_trace():
    return [
        {"type": "text", "text": "Counter is 1"},
        {
            "type": "tool", "tool": "web_use", "tool_call_id": "call-1",
            "input": '{"command": "observe"}', "result": "Counter: 1", "is_error": False,
        },
        {"type": "text", "text": "Counter is 2"},
        {
            "type": "tool", "tool": "web_use", "tool_call_id": "call-2",
            "input": '{"command": "act"}', "result": "Counter: 2", "is_error": False,
        },
        {"type": "text", "text": "Counter is 3"},
        {
            "type": "tool", "tool": "web_use", "tool_call_id": "call-3",
            "input": '{"command": "observe"}', "result": "Counter: 3", "is_error": False,
        },
    ]


def _blob_checkpoint(state):
    import hashlib
    from openprogram.agent.continuation import canonical_json_bytes

    payload_raw = canonical_json_bytes(dict(state.payload))
    digest = hashlib.sha256(payload_raw).hexdigest()
    main = {
        "ref": f"execstate://sha256/{digest}",
        "sha256": digest,
        "byte_length": len(payload_raw),
        "media_type": "application/json",
        "schema_version": 1,
    }
    blobs = dict(state.blob_payloads)
    blobs[main["ref"]] = payload_raw

    class _Store:
        def get_state_blob(self, execution_id, ref):
            raw = blobs.get(ref)
            if raw is None:
                return None
            return {
                "ref": ref,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "byte_length": len(raw),
                "media_type": "application/json",
                "schema_version": 1,
                "payload": raw,
            }

    return _Store(), SimpleNamespace(
        execution_id="exec-display", state_refs={"agent_checkpoint": main},
    )


def test_unpatched_continuation_persist_keeps_prior_display_cards(
    tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from openprogram.agent.continuation import AgentCheckpointV1, AgentContinuation
    from openprogram.agent.dispatcher import loop_runner
    from openprogram.agent.types import AgentTool, AgentToolResult
    from openprogram.providers.types import (
        EventDone,
        EventStart,
        EventTextDelta,
        EventTextEnd,
        EventTextStart,
        Model,
        Usage,
    )

    session_id = "local_continuation_display"
    tmp_db.create_session(session_id, "main", source="component")
    writer = SessionNodeWriter(tmp_db, session_id)
    writer.append(Call(
        id=USER_ID, created_at=1.0, role=ROLE_USER, output="continue pages",
        metadata={"source": "component"},
    ))
    writer.append(Call(
        id=ASSISTANT_ID, created_at=1.1, role=ROLE_LLM, output="",
        predecessor=USER_ID,
        metadata={"status": "running", "source": "component"},
    ))
    writer.append(Call(
        id=f"{ASSISTANT_ID}_t_call-1",
        created_at=1.2, role=ROLE_CODE, name="web_use",
        input={"command": "observe"}, output="Counter: 1",
        caller=ASSISTANT_ID,
        metadata={"tool_call_id": "call-1", "is_error": False},
    ))

    request = TurnRequest(
        session_id=session_id, user_text="continue pages",
        agent_id="main", source="component", user_msg_id=USER_ID,
        user_already_persisted=True,
    )
    request._execution_revision_id = "test-revision"
    model = Model(
        id="fake", name="fake", api="openai-completions", provider="openai",
        base_url="https://example.invalid/v1",
    )

    async def execute(_call_id, _args, _cancel, _update):
        return AgentToolResult(content=[TextContent(text="ok")])

    tool = AgentTool(
        name="web_use", description="web_use", parameters={"type": "object"},
        label="web_use", execute=execute,
    )
    snapshot = runtime_contract_snapshot(
        model=model, system_prompt="sys", tools=[tool], request=request,
    )
    last = AssistantMessage(
        content=[
            TextContent(text="Counter is 3"),
            ToolCall(id="call-3", name="web_use", arguments={"command": "observe"}),
        ],
        api="openai-completions", provider="openai", model="fake",
        timestamp=1, stop_reason="toolUse",
    )
    last_result = ToolResultMessage(
        tool_call_id="call-3", tool_name="web_use",
        content=[TextContent(text="Counter: 3")], timestamp=1,
    )

    def _state(display):
        return AgentCheckpointV1.build(
            safe_point={
                "kind": "agent.tool.action.after", "step_id": "after_tool:p",
                "phase": "after_tool", "sentinel": "resume-from-checkpoint",
            },
            frontier=[{"step_id": "after_tool:p", "phase": "after_tool", "branch_id": "main"}],
            turn={
                "user_message_id": USER_ID, "assistant_message_id": ASSISTANT_ID,
                "base_history_head_id": USER_ID,
            },
            assistant_message=last.model_dump(mode="json"),
            tool_results=[last_result.model_dump(mode="json")],
            resolved_snapshot=snapshot,
            provider_action_id="provider-action",
            tool_call_ids=["call-3"],
            next_tool_index=1,
            repeat_failures={},
            completed_actions=[
                {"action_id": "provider-action", "input_hash": "context-hash"},
                {"action_id": "tool-action-1", "input_hash": "tool-hash"},
            ],
            terminal_effect_receipts=[
                {
                    "effect_id": "effect-provider", "frontier_step_id": "provider:p",
                    "action_id": "provider-action", "outcome": "committed",
                    "receipt": {"provider_request_id": "saved-request"},
                },
                {
                    "effect_id": "effect-tool", "frontier_step_id": "after_tool:p",
                    "action_id": "tool-action-1", "outcome": "committed",
                    "receipt": {"tool_call_id": "call-3"},
                },
            ],
            turn_display=display,
        )

    store, checkpoint = _blob_checkpoint(_state(_display_trace()))
    continuation = AgentContinuation.from_checkpoint(
        store=store, checkpoint=checkpoint, request=request,
    )
    assert [card.get("tool_call_id") for card in continuation.display if card.get("type") == "tool"] == [
        "call-1", "call-2", "call-3",
    ]
    assert continuation.assistant_message.content[0].text == "Counter is 3"

    def fake_resolve(req, assistant_msg_id=None, on_event=None,
                     saved_runtime_contract=None):
        del req, assistant_msg_id, on_event, saved_runtime_contract
        return {"name": "main"}, [tool], "sys", "sys", model, snapshot

    monkeypatch.setattr(loop_runner, "resolve_agent_runtime", fake_resolve)

    def _stream(text: str):
        async def _fn(_model, _context, _options):
            now = int(time.time() * 1000)
            partial = AssistantMessage(
                content=[TextContent(text="")], api="completion",
                provider="openai", model="fake", timestamp=now,
            )
            yield EventStart(partial=partial)
            yield EventTextStart(content_index=0, partial=partial)
            yield EventTextDelta(content_index=0, delta=text, partial=AssistantMessage(
                content=[TextContent(text=text)], api="completion",
                provider="openai", model="fake", timestamp=now,
            ))
            yield EventTextEnd(content_index=0, content=text, partial=AssistantMessage(
                content=[TextContent(text=text)], api="completion",
                provider="openai", model="fake", timestamp=now,
            ))
            yield EventDone(
                reason="stop",
                message=AssistantMessage(
                    content=[TextContent(text=text)], api="completion",
                    provider="openai", model="fake", timestamp=now,
                    usage=Usage(input_tokens=2, output_tokens=2),
                    stop_reason="stop",
                ),
            )
        return _fn

    orig = D._run_loop_blocking

    def _wrap(**kwargs):
        kwargs["stream_fn"] = _stream("resume-1")
        return orig(**kwargs)

    with patch.object(D, "_run_loop_blocking", _wrap):
        first = D.process_agent_continuation(continuation, on_event=lambda _e: None)
    assert first.failed is False
    assistant = next(row for row in tmp_db.get_messages(session_id) if row["id"] == ASSISTANT_ID)
    payload = assistant.get("extra") or (assistant.get("metadata") or {}).get("extra")
    if isinstance(payload, str):
        payload = json.loads(payload)
    first_blocks = payload["blocks"]
    labels = [block.get("tool_call_id") or block.get("text") for block in first_blocks]
    assert labels[:6] == [
        "Counter is 1", "call-1", "Counter is 2", "call-2", "Counter is 3", "call-3",
    ]
    assert "resume-1" in labels
    assert first_blocks[1]["result"] == "Counter: 1"
    assert labels.count("call-1") == 1
    assert labels.count("Counter is 3") == 1
    pre_nodes = [
        row for row in tmp_db.get_messages(session_id)
        if row.get("id") == f"{ASSISTANT_ID}_t_call-1"
    ]
    assert len(pre_nodes) == 1

    store2, checkpoint2 = _blob_checkpoint(_state(first_blocks))
    second = AgentContinuation.from_checkpoint(
        store=store2, checkpoint=checkpoint2, request=request,
    )

    def _wrap2(**kwargs):
        kwargs["stream_fn"] = _stream("resume-2")
        return orig(**kwargs)

    with patch.object(D, "_run_loop_blocking", _wrap2):
        done = D.process_agent_continuation(second, on_event=lambda _e: None)
    assert done.failed is False
    assistant = next(row for row in tmp_db.get_messages(session_id) if row["id"] == ASSISTANT_ID)
    payload = assistant.get("extra") or (assistant.get("metadata") or {}).get("extra")
    if isinstance(payload, str):
        payload = json.loads(payload)
    second_labels = [
        block.get("tool_call_id") or block.get("text") for block in payload["blocks"]
    ]
    assert second_labels[:6] == labels[:6]
    assert "resume-1" in second_labels
    assert "resume-2" in second_labels
    assert second_labels.count("call-1") == 1
    assert second_labels.count("resume-1") == 1
    assert len([
        row for row in tmp_db.get_messages(session_id)
        if row.get("id") == f"{ASSISTANT_ID}_t_call-1"
    ]) == 1


def _text_done_stream(text: str):
    import time
    from openprogram.providers.types import EventDone, EventStart, Usage

    async def _fn(_model, _context, _options):
        now = int(time.time() * 1000)
        msg = AssistantMessage(
            content=[TextContent(text=text)], api="openai-completions",
            provider="openai", model="fake", timestamp=now,
            usage=Usage(input_tokens=2, output_tokens=2), stop_reason="stop",
        )
        yield EventStart(partial=msg)
        yield EventDone(reason="stop", message=msg)
    return _fn


def test_continuation_appends_new_repeated_commentary(
    tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.agent.continuation import AgentCheckpointV1, AgentContinuation
    from openprogram.agent.dispatcher import loop_runner
    from openprogram.agent.types import AgentTool, AgentToolResult
    from openprogram.providers.types import Model

    session_id = "local_continuation_repeated"
    tmp_db.create_session(session_id, "main", source="component")
    writer = SessionNodeWriter(tmp_db, session_id)
    writer.append(Call(
        id=USER_ID, created_at=1.0, role=ROLE_USER, output="continue pages",
        metadata={"source": "component"},
    ))
    writer.append(Call(
        id=ASSISTANT_ID, created_at=1.1, role=ROLE_LLM, output="",
        predecessor=USER_ID, metadata={"status": "running"},
    ))
    request = TurnRequest(
        session_id=session_id, user_text="continue pages",
        agent_id="main", source="component", user_msg_id=USER_ID,
        user_already_persisted=True,
    )
    request._execution_revision_id = "test-revision"
    model = Model(
        id="fake", name="fake", api="openai-completions", provider="openai",
        base_url="https://example.invalid/v1",
    )

    async def execute(_call_id, _args, _cancel, _update):
        return AgentToolResult(content=[TextContent(text="ok")])

    tool = AgentTool(
        name="web_use", description="web_use", parameters={"type": "object"},
        label="web_use", execute=execute,
    )
    snapshot = runtime_contract_snapshot(
        model=model, system_prompt="sys", tools=[tool], request=request,
    )
    last = AssistantMessage(
        content=[
            TextContent(text="saved-now"),
            ToolCall(id="call-3", name="web_use", arguments={"command": "observe"}),
        ],
        api="openai-completions", provider="openai", model="fake",
        timestamp=1, stop_reason="toolUse",
    )
    last_result = ToolResultMessage(
        tool_call_id="call-3", tool_name="web_use",
        content=[TextContent(text="ok")], timestamp=1,
    )
    display = [
        {"type": "text", "text": "repeated"},
        {
            "type": "tool", "tool": "web_use", "tool_call_id": "call-1",
            "result": "ok", "is_error": False,
        },
        {"type": "text", "text": "saved-now"},
        {
            "type": "tool", "tool": "web_use", "tool_call_id": "call-3",
            "result": "ok", "is_error": False,
        },
    ]
    state = AgentCheckpointV1.build(
        safe_point={
            "kind": "agent.tool.action.after", "step_id": "after_tool:p",
            "phase": "after_tool", "sentinel": "resume-from-checkpoint",
        },
        frontier=[{"step_id": "after_tool:p", "phase": "after_tool", "branch_id": "main"}],
        turn={
            "user_message_id": USER_ID, "assistant_message_id": ASSISTANT_ID,
            "base_history_head_id": USER_ID,
        },
        assistant_message=last.model_dump(mode="json"),
        tool_results=[last_result.model_dump(mode="json")],
        resolved_snapshot=snapshot,
        provider_action_id="provider-action",
        tool_call_ids=["call-3"],
        next_tool_index=1,
        repeat_failures={},
        completed_actions=[
            {"action_id": "provider-action", "input_hash": "context-hash"},
            {"action_id": "tool-action-1", "input_hash": "tool-hash"},
        ],
        terminal_effect_receipts=[
            {
                "effect_id": "effect-provider", "frontier_step_id": "provider:p",
                "action_id": "provider-action", "outcome": "committed",
                "receipt": {"n": 1},
            },
            {
                "effect_id": "effect-tool", "frontier_step_id": "after_tool:p",
                "action_id": "tool-action-1", "outcome": "committed",
                "receipt": {"tool_call_id": "call-3"},
            },
        ],
        turn_display=display,
    )
    store, checkpoint = _blob_checkpoint(state)
    continuation = AgentContinuation.from_checkpoint(
        store=store, checkpoint=checkpoint, request=request,
    )
    monkeypatch.setattr(
        loop_runner, "resolve_agent_runtime",
        lambda *a, **k: ({"name": "main"}, [tool], "sys", "sys", model, snapshot),
    )
    orig = D._run_loop_blocking

    def _wrap(**kwargs):
        kwargs["stream_fn"] = _text_done_stream("repeated")
        return orig(**kwargs)

    with patch.object(D, "_run_loop_blocking", _wrap):
        result = D.process_agent_continuation(continuation, on_event=lambda _e: None)
    assert result.failed is False
    assistant = next(row for row in tmp_db.get_messages(session_id) if row["id"] == ASSISTANT_ID)
    payload = assistant.get("extra") or (assistant.get("metadata") or {}).get("extra")
    if isinstance(payload, str):
        payload = json.loads(payload)
    labels = [block.get("tool_call_id") or block.get("text") for block in payload["blocks"]]
    assert labels == ["repeated", "call-1", "saved-now", "call-3", "repeated"]
    assert labels.count("repeated") == 2
    assert labels.count("saved-now") == 1


def test_normal_turn_keeps_identical_commentary_from_two_decisions(
    tmp_db: SessionDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.agent.dispatcher import loop_runner
    from openprogram.agent.types import AgentTool, AgentToolResult
    from openprogram.providers.types import EventDone, EventStart, Model, Usage

    model = Model(
        id="fake", name="fake", api="openai-completions", provider="openai",
        base_url="https://example.invalid/v1",
    )

    async def execute(_call_id, _args, _cancel, _update):
        return AgentToolResult(content=[TextContent(text="ok")])

    tool = AgentTool(
        name="web_use", description="web_use", parameters={"type": "object"},
        label="web_use", execute=execute,
    )
    snapshot = runtime_contract_snapshot(
        model=model, system_prompt="sys", tools=[tool],
        request=TurnRequest(
            session_id="c-repeat", user_text="hi", agent_id="main", source="tui",
        ),
    )
    monkeypatch.setattr(
        loop_runner, "resolve_agent_runtime",
        lambda *a, **k: ({"name": "main"}, [tool], "sys", "sys", model, snapshot),
    )
    calls = {"n": 0}

    def stream_fn(_model, _context, _options):
        async def gen():
            n = calls["n"]
            calls["n"] += 1
            if n == 0:
                msg = AssistantMessage(
                    content=[
                        TextContent(text="Working"),
                        ToolCall(id="echo-1", name="web_use", arguments={}),
                    ],
                    api="openai-completions", provider="openai", model="fake",
                    timestamp=1, stop_reason="toolUse",
                )
                yield EventStart(partial=msg)
                yield EventDone(reason="toolUse", message=msg)
            else:
                msg = AssistantMessage(
                    content=[TextContent(text="Working")],
                    api="openai-completions", provider="openai", model="fake",
                    timestamp=2, stop_reason="stop",
                    usage=Usage(input_tokens=1, output_tokens=1),
                )
                yield EventStart(partial=msg)
                yield EventDone(reason="stop", message=msg)
        return gen()

    orig = D._run_loop_blocking

    def _wrap(**kwargs):
        kwargs["stream_fn"] = stream_fn
        return orig(**kwargs)

    with patch.object(D, "_run_loop_blocking", _wrap):
        result = D.process_user_turn(
            D.TurnRequest(session_id="c-repeat", user_text="hi", agent_id="main", source="tui"),
        )
    assert result.failed is False
    assistant = next(row for row in tmp_db.get_messages("c-repeat") if row["role"] == "assistant")
    payload = assistant.get("extra") or (assistant.get("metadata") or {}).get("extra")
    if isinstance(payload, str):
        payload = json.loads(payload)
    texts = [block.get("text") for block in payload["blocks"] if block.get("type") == "text"]
    assert texts.count("Working") == 2

