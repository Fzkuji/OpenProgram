"""Webui's "query" action now routes through ``process_user_turn``.

Replaces the old ``runtime.exec(content=chat_content)`` direct call.
This test exercises ``_execute_in_context(action='query')`` end-to-
end with a fake stream_fn so we don't pay a real provider call.

What must hold after the migration:
  - The user message stays persisted exactly once (the WS handler
    pre-appends it; dispatcher must not double-write).
  - The assistant reply lands in SessionDB via the dispatcher, not
    via webui's _append_msg.
  - The active branch (get_branch) shows user → assistant in order.
  - A "result" chat_response envelope reaches the WS broadcast hook
    so the frontend receives the final text.
  - SessionDB stays the unified storage for both channels and webui
    paths (regression guard against accidental file-based fallback).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import patch

import pytest

from openprogram.agent import dispatcher as D
from openprogram.agent import run_control
from openprogram.agent.session_db import SessionDB
from openprogram.providers.types import (
    AssistantMessage,
    AssistantMessageEvent,
    EventDone,
    EventStart,
    EventTextDelta,
    EventTextEnd,
    EventTextStart,
    Model,
    TextContent,
    Usage,
)


def _stub_model() -> Model:
    return Model(id="stub", name="stub", api="completion",
                 provider="openai", base_url="https://x")


def _build_partial(t: str = "") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=t)] if t else [],
        api="completion", provider="openai", model="stub",
        timestamp=int(time.time() * 1000),
    )


def _build_final(t: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=t)],
        api="completion", provider="openai", model="stub",
        usage=Usage(input=1, output=1), stop_reason="stop",
        timestamp=int(time.time() * 1000),
    )


def make_text_stream(text: str):
    async def _fn(model, ctx, opts) -> AsyncGenerator[AssistantMessageEvent, None]:
        yield EventStart(partial=_build_partial(""))
        yield EventTextStart(content_index=0, partial=_build_partial(""))
        yield EventTextDelta(content_index=0, delta=text, partial=_build_partial(text))
        yield EventTextEnd(content_index=0, content=text, partial=_build_partial(text))
        yield EventDone(reason="stop", message=_build_final(text))
    return _fn


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Webui module + isolated SessionDB. Stubs out the runtime
    creation path so we don't try to dial a provider."""
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.agent.session_db.default_db",
                        lambda: db)
    monkeypatch.setattr(D, "_resolve_model",
                        lambda profile, override=None: _stub_model())
    monkeypatch.setattr(D, "_load_agent_profile",
                        lambda agent_id: {"id": agent_id,
                                            "system_prompt": "",
                                            "tools": []})
    from openprogram.webui import server as srv
    srv._sessions.clear()
    srv._msg_cache.clear()

    # Stub _get_session_runtime so we don't try to instantiate a real
    # CLI provider runtime — the dispatcher path doesn't actually
    # use it for chat, but _execute_in_context still resolves it.
    class _FakeRuntime:
        on_stream = None
        last_blocks = []
        model = "stub"
        _session_id = None
    monkeypatch.setattr(srv, "_get_session_runtime",
                        lambda conv_id, msg_id=None: _FakeRuntime())
    # Bypass thinking-effort apply (it pokes at runtime internals)
    monkeypatch.setattr(srv, "_apply_thinking_effort",
                        lambda runtime, eff: None)
    # Skip context_stats broadcast — it reads runtime._cumulative
    monkeypatch.setattr(srv, "_broadcast_context_stats",
                        lambda *a, **kw: None)
    # Capture broadcasts from _broadcast_chat_response
    captured: list[dict] = []
    def _capture(conv_id, msg_id, payload):
        captured.append({"conv_id": conv_id, "msg_id": msg_id,
                          "payload": payload})
    monkeypatch.setattr(srv, "_broadcast_chat_response", _capture)
    # Skip channel outbound forwarding (no real channel client)
    monkeypatch.setattr(srv, "_load_agent_session_meta",
                        lambda conv_id: None)
    return srv, db, captured


def test_query_action_writes_via_dispatcher(env, monkeypatch: pytest.MonkeyPatch) -> None:
    srv, db, captured = env

    # Pre-append the user message (mimics the WS chat handler at
    # server.py around line 1972).
    conv = srv._get_or_create_session("c1", agent_id="main")
    user_msg_id = "u-frontend"
    srv._append_msg(conv, {
        "id": user_msg_id, "role": "user", "content": "hello",
        "timestamp": time.time(), "source": "web",
    })

    # Patch the dispatcher's loop with our scripted stream
    fake = make_text_stream("Hi from dispatcher")
    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, **_):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake)

    with patch.object(D, "_run_loop_blocking", _w):
        srv._execute_in_context(
            "c1", user_msg_id, "query",
            query="hello", thinking_effort=None,
            tools_flag=None,
        )

    # SessionDB has the pre-appended user msg + dispatcher's
    # assistant reply. No duplicate user row.
    rows = db.get_messages("c1")
    by_role = {}
    for r in rows:
        by_role.setdefault(r["role"], []).append(r["id"])
    assert by_role.get("user", []) == [user_msg_id]
    assert len(by_role.get("assistant", [])) == 1

    # Active branch is user → assistant in order
    branch = db.get_branch("c1")
    assert [m["role"] for m in branch] == ["user", "assistant"]
    assert branch[1]["content"] == "Hi from dispatcher"

    # The frontend got a "result" envelope with the final text
    results = [c for c in captured
               if c["payload"].get("type") == "result"]
    assert len(results) == 1
    assert results[0]["payload"]["content"] == "Hi from dispatcher"

    # Stream events fanned out as legacy "stream_event" envelopes
    stream_events = [c for c in captured
                     if c["payload"].get("type") == "stream_event"]
    assert any(e["payload"]["event"].get("type") == "text"
               for e in stream_events)
    assert all(
        "output_attempt" not in e["payload"]["event"]
        for e in stream_events
        if e["payload"]["event"].get("type") == "text"
    )


def test_query_action_failure_emits_error_envelope(env) -> None:
    srv, db, captured = env

    conv = srv._get_or_create_session("c1", agent_id="main")
    user_msg_id = "u-fail"
    srv._append_msg(conv, {
        "id": user_msg_id, "role": "user", "content": "fail me",
        "timestamp": time.time(), "source": "web",
    })

    async def _angry(model, ctx, opts):
        if False:
            yield None
        raise RuntimeError("provider boom")

    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, **_):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=_angry)

    with patch.object(D, "_run_loop_blocking", _w):
        srv._execute_in_context(
            "c1", user_msg_id, "query",
            query="fail me", thinking_effort=None,
            tools_flag=None,
        )

    err_payloads = [c for c in captured
                    if c["payload"].get("type") == "error"]
    assert len(err_payloads) == 1
    assert "boom" in err_payloads[0]["payload"]["content"].lower()


def test_structured_query_retries_and_returns_typed_result(env) -> None:
    srv, db, captured = env
    conv = srv._get_or_create_session("structured", agent_id="main")
    msg_id = "u-structured"
    srv._append_msg(conv, {
        "id": msg_id, "role": "user", "content": "answer",
        "timestamp": time.time(), "source": "web",
    })
    replies = iter(['{"answer":"bad"}', '{"answer":3}'])

    async def _stream(model, ctx, opts):
        text = next(replies)
        yield EventStart(partial=_build_partial(""))
        yield EventTextStart(content_index=0, partial=_build_partial(""))
        yield EventTextDelta(content_index=0, delta=text, partial=_build_partial(text))
        yield EventTextEnd(content_index=0, content=text, partial=_build_partial(text))
        yield EventDone(reason="stop", message=_build_final(text))

    response_format = {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {"answer": {"type": "integer"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
        "fallback": "prompt",
    }
    from openprogram.providers.structured_output import normalize_response_format
    response_format = normalize_response_format(response_format)
    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, **_):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=_stream)

    with patch.object(D, "_run_loop_blocking", _w):
        srv._execute_in_context(
            "structured", msg_id, "query", query="answer",
            thinking_effort=None, tools_flag=None,
            response_format=response_format,
        )

    retry = [c["payload"]["event"] for c in captured
             if c["payload"].get("type") == "stream_event"
             and c["payload"].get("event", {}).get("type") == "structured_output_retry"]
    result = [c["payload"] for c in captured if c["payload"].get("type") == "result"]
    assert retry[0]["attempt"] == 1
    assert result[-1]["structured_output"] == {"answer": 3}
    assert result[-1]["structured_output_mode"] == "prompt"
    assert result[-1]["attempt"] == 2
    persisted = db.get_branch("structured")[-1]
    assert persisted["content"] == '{"answer":3}'
    assert persisted["structured_output"] == {"answer": 3}
    assert persisted["structured_output_mode"] == "prompt"
    assert persisted["structured_output_attempt"] == 2


def test_structured_query_error_is_typed_and_does_not_expose_candidate(env) -> None:
    srv, _db, captured = env
    conv = srv._get_or_create_session("structured-error", agent_id="main")
    msg_id = "u-structured-error"
    srv._append_msg(conv, {
        "id": msg_id, "role": "user", "content": "answer",
        "timestamp": time.time(), "source": "web",
    })

    async def _stream(model, ctx, opts):
        text = '{"answer":"secret candidate"}'
        yield EventStart(partial=_build_partial(""))
        yield EventTextStart(content_index=0, partial=_build_partial(""))
        yield EventTextDelta(content_index=0, delta=text, partial=_build_partial(text))
        yield EventTextEnd(content_index=0, content=text, partial=_build_partial(text))
        yield EventDone(reason="stop", message=_build_final(text))

    response_format = {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {"answer": {"type": "integer"}},
            "required": ["answer"],
        },
        "fallback": "prompt",
    }
    from openprogram.providers.structured_output import normalize_response_format
    response_format = normalize_response_format(response_format)
    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, **_):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=_stream)

    with patch.object(D, "_run_loop_blocking", _w):
        srv._execute_in_context(
            "structured-error", msg_id, "query", query="answer",
            thinking_effort=None, tools_flag=None,
            response_format=response_format,
        )

    errors = [c["payload"] for c in captured if c["payload"].get("type") == "error"]
    assert errors[-1]["code"] == "validation_failed"
    assert errors[-1]["attempts"] == 2
    assert errors[-1]["issues"][0]["code"] == "schema_violation"
    assert "secret candidate" not in str(errors[-1])


def test_query_action_rejects_session_reserved_by_mcp_without_replacing_token(
    env,
) -> None:
    srv, db, captured = env
    conv = srv._get_or_create_session("c1", agent_id="main")
    srv._append_msg(conv, {
        "id": "u-busy", "role": "user", "content": "busy",
        "timestamp": time.time(), "source": "web",
    })
    mcp_event = run_control.CancelToken("c1").event
    assert run_control.claim_cancel_event("c1", mcp_event)
    run_control.register_active_runtime("c1", object())

    try:
        srv._execute_in_context(
            "c1", "u-busy", "query", query="busy",
            thinking_effort=None, tools_flag=None,
        )
        token = run_control.current_token("c1")
        assert token is not None
        assert token.event is mcp_event
        assert run_control.has_active_runtime("c1")
        assert db.get_messages("c1")[-1]["role"] == "user"
        errors = [
            item["payload"] for item in captured
            if item["payload"].get("type") == "error"
        ]
        assert len(errors) == 1
        assert "already active" in errors[0]["content"]
    finally:
        run_control.unregister_active_runtime("c1")
        run_control.unregister_cancel_event("c1", mcp_event)


def test_write_tool_checkpoints_when_dag_runtime_unavailable(
    env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A WebUI turn that edits a file must leave a checkpoint behind.

    Regression: ``_store`` used to be bound inside the same try/except as
    the DAG runtime, so a ``create_runtime()`` failure (no provider, or an
    exhausted auth pool — routine when the chat runtime is a different
    provider) left it unbound for the whole turn. ``checkpoint_before_edit``
    needs ``_store`` AND ``_current_turn_id``, so it silently no-op'd and
    no ``file_backups/`` was ever written — which made list_turn_files
    empty and the whole per-turn file review UI dark.

    Forcing create_runtime to raise reproduces the real failure exactly.
    """
    from openprogram.store.snapshot.checkpoint.paths import turn_manifest_path
    from openprogram.programs.tools.files.write import write as write_tool

    srv, db, _captured = env

    # The env fixture only patches `session_db.default_db`; turn_files
    # (like all read-side code) resolves `default_store()` directly, so
    # pin the singleton too or it reads the developer's real sessions.
    monkeypatch.setattr(
        "openprogram.store.session.session_store._default_store", db,
        raising=False,
    )
    # Other suites setattr a real attribute over the lazy
    # `openprogram.store.default_store` re-export, which permanently
    # shadows the __getattr__ hook — pin it so this passes in-suite too.
    monkeypatch.setattr(
        "openprogram.store.default_store", lambda: db, raising=False,
    )

    monkeypatch.setattr(
        "openprogram.providers.registry.create_runtime",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("auth pool exhausted")),
    )

    target = tmp_path / "undo_test.txt"
    monkeypatch.setattr(
        "openprogram.worktree.context.current_worktree_path",
        lambda: str(tmp_path),
    )
    user_msg_id = "u-write"
    assistant_msg_id = user_msg_id + "_reply"

    conv = srv._get_or_create_session("c1", agent_id="main")
    srv._append_msg(conv, {
        "id": user_msg_id, "role": "user", "content": "create the file",
        "timestamp": time.time(), "source": "web",
    })

    orig = D._run_loop_blocking

    # Stand in for the LLM issuing a write tool call: run the real write
    # tool mid-turn, exactly where the agent loop would. `write` is an
    # AgentTool, so it goes through its async execute().
    def _w(*, req, history, on_event, cancel_event, **_):
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            res = loop.run_until_complete(write_tool.execute(
                "call-write",
                {"file_path": str(target), "content": "hello from the agent\n"},
                None, None,
            ))
        finally:
            loop.close()
        assert "Error" not in str(res), f"write tool failed: {res}"
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event,
                    stream_fn=make_text_stream("created it"))

    with patch.object(D, "_run_loop_blocking", _w):
        srv._execute_in_context(
            "c1", user_msg_id, "query",
            query="create the file", thinking_effort=None, tools_flag=None,
        )

    assert target.read_text() == "hello from the agent\n"

    # The checkpoint exists and is keyed to THIS turn — that manifest is
    # what list_turn_files / turn_file_diff / revert_turn all read.
    session_dir = db._session_dir("c1")
    manifest = turn_manifest_path(Path(session_dir), assistant_msg_id)
    assert manifest.exists(), "no file_backups manifest — checkpoint no-op'd"

    from openprogram.store.snapshot.checkpoint import CheckpointStore
    backed = CheckpointStore(Path(session_dir)).list_backed_paths(assistant_msg_id)
    assert str(target) in backed

    # ...and the WS action the UI actually calls now returns that file,
    # which is the symptom the user reported (empty list → no card).
    from openprogram.webui.ws_actions import turn_files as tf
    listed = tf._list_files("c1", assistant_msg_id)
    assert [f["path"] for f in listed["files"]] == [str(target)]
    assert listed["files"][0]["op"] == "add"


def test_shadow_git_commits_on_webui_turn(
    env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """finalize_turn runs on the WebUI path, so the shadow-git commit
    (and its before/after stamp) happens for web chats too — that stamp
    is what makes turn_file_diff exact rather than approximate."""
    from types import SimpleNamespace
    from openprogram.programs.tools.files.write import write as write_tool

    srv, db, _captured = env

    # See the sibling test: pin the read-side singleton as well.
    monkeypatch.setattr(
        "openprogram.store.session.session_store._default_store", db,
        raising=False,
    )
    # Other suites setattr a real attribute over the lazy
    # `openprogram.store.default_store` re-export, which permanently
    # shadows the __getattr__ hook — pin it so this passes in-suite too.
    monkeypatch.setattr(
        "openprogram.store.default_store", lambda: db, raising=False,
    )

    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setattr(
        "openprogram.worktree.context.current_worktree_path",
        lambda: str(project),
    )
    target = project / "undo_test.txt"
    shadow_root = tmp_path / "shadow"
    shadow_root.mkdir()

    user_msg_id = "u-shadow"
    assistant_msg_id = user_msg_id + "_reply"
    conv = srv._get_or_create_session("c1", agent_id="main")
    srv._append_msg(conv, {
        "id": user_msg_id, "role": "user", "content": "create it",
        "timestamp": time.time(), "source": "web",
    })

    orig = D._run_loop_blocking

    def _w(*, req, history, on_event, cancel_event, **_):
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(write_tool.execute(
                "call-write",
                {"file_path": str(target), "content": "shadowed\n"},
                None, None,
            ))
        finally:
            loop.close()
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event,
                    stream_fn=make_text_stream("done"))

    with patch("openprogram.store.shadow_git.store._shadow_root",
               return_value=shadow_root), \
         patch("openprogram.store.project.project_commit._project_for",
               return_value=SimpleNamespace(path=str(project))), \
         patch.object(D, "_run_loop_blocking", _w):
        srv._execute_in_context(
            "c1", user_msg_id, "query",
            query="create it", thinking_effort=None, tools_flag=None,
        )

    _git, idx = db._open("c1")
    meta = (idx.nodes_by_id[assistant_msg_id].metadata or {}).get("shadow_git")
    assert meta and meta.get("after"), "no shadow_git stamp from the webui turn"


def _capture_permission_mode(monkeypatch, stream_text="ok"):
    seen: dict[str, str] = {}
    orig = D._run_loop_blocking
    fake = make_text_stream(stream_text)

    def _w(*, req, history, on_event, cancel_event, **_):
        seen["mode"] = req.permission_mode
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake)

    monkeypatch.setattr(D, "_run_loop_blocking", _w)
    return seen


def test_missing_permission_mode_defaults_to_ask(env, monkeypatch) -> None:
    srv, db, _captured = env
    conv = srv._get_or_create_session("c1", agent_id="main")
    srv._append_msg(conv, {
        "id": "u-perm", "role": "user", "content": "hi",
        "timestamp": time.time(), "source": "web",
    })
    seen = _capture_permission_mode(monkeypatch)
    srv._execute_in_context("c1", "u-perm", "query", query="hi")
    assert seen["mode"] == "ask"


def test_invalid_permission_mode_fails_safe_to_ask(env, monkeypatch) -> None:
    srv, _db, _captured = env
    conv = srv._get_or_create_session("c1", agent_id="main")
    srv._append_msg(conv, {
        "id": "u-bad", "role": "user", "content": "hi",
        "timestamp": time.time(), "source": "web",
    })
    seen = _capture_permission_mode(monkeypatch)
    srv._execute_in_context(
        "c1", "u-bad", "query", query="hi", permission_mode="not-a-mode",
    )
    assert seen["mode"] == "ask"


def test_inherit_uses_project_default(env, monkeypatch) -> None:
    srv, _db, _captured = env
    conv = srv._get_or_create_session("c1", agent_id="main")
    srv._append_msg(conv, {
        "id": "u-inh", "role": "user", "content": "hi",
        "timestamp": time.time(), "source": "web",
    })
    monkeypatch.setattr(
        "openprogram.agent.session_config.project_defaults",
        lambda sid: {"permission_mode": "acceptEdits"},
    )
    seen = _capture_permission_mode(monkeypatch)
    srv._execute_in_context(
        "c1", "u-inh", "query", query="hi", permission_mode="inherit",
    )
    assert seen["mode"] == "acceptEdits"


def test_explicit_bypass_is_kept(env, monkeypatch) -> None:
    srv, _db, _captured = env
    conv = srv._get_or_create_session("c1", agent_id="main")
    srv._append_msg(conv, {
        "id": "u-byp", "role": "user", "content": "hi",
        "timestamp": time.time(), "source": "web",
    })
    seen = _capture_permission_mode(monkeypatch)
    srv._execute_in_context(
        "c1", "u-byp", "query", query="hi", permission_mode="bypass",
    )
    assert seen["mode"] == "bypass"
