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
    # Skip the active-runtime registry tracking
    monkeypatch.setattr(srv, "_register_active_runtime",
                        lambda conv_id, runtime: None)
    monkeypatch.setattr(srv, "_unregister_active_runtime",
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
