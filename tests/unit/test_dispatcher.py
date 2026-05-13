"""Coverage for the dispatcher.process_user_turn pipeline.

Real ``agent_loop()`` calls go through provider runtimes (network,
auth) — too heavy for unit tests. We patch ``_run_loop_blocking`` at
the seam so we can assert:

  * SessionDB receives both user + assistant messages with proper
    parent_id / timestamp / source linkage
  * `chat_response` envelopes hit ``on_event`` in the right order
  * Errors get surfaced as a ``system`` message + ``error`` envelope,
    not a raw exception
  * Approval flow blocks until ApprovalRegistry.resolve() fires
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from openprogram.agent import dispatcher as D
from openprogram.agent.session_db import SessionDB


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    """Replace the dispatcher's default_db() with one rooted in tmp_path."""
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db",
        lambda: db,
    )
    # Also patch the inline import inside dispatcher's module-level
    # default_db reference, in case the module already cached it.
    return db


@pytest.fixture
def captured() -> list[dict]:
    """Collected on_event payloads for assertion."""
    return []


@pytest.fixture
def collector(captured: list[dict]):
    return captured.append


def _stub_loop_returning(text: str, *,
                         tool_calls: list[dict] | None = None,
                         usage: dict | None = None):
    """Build a _run_loop_blocking replacement that emits a few stream
    events then returns the given final text/usage/tool_calls."""
    def _stub(*, req: D.TurnRequest, history, on_event, cancel_event):
        # Emit a couple of stream events so consumers see live deltas.
        on_event({"type": "chat_response",
                  "data": {"type": "stream_event",
                           "event": {"type": "text", "text": text[:5]}}})
        on_event({"type": "chat_response",
                  "data": {"type": "stream_event",
                           "event": {"type": "text", "text": text[5:]}}})
        return text, usage or {"input_tokens": 10, "output_tokens": 4}, list(tool_calls or [])
    return _stub


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_persists_user_and_assistant(tmp_db: SessionDB, collector) -> None:
    with patch.object(D, "_run_loop_blocking",
                      _stub_loop_returning("hello world")):
        result = D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="hi", agent_id="main", source="tui"),
            on_event=collector,
        )

    assert result.failed is False
    assert result.final_text == "hello world"
    assert result.assistant_msg_id

    msgs = tmp_db.get_messages("c1")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hi"
    assert msgs[0]["source"] == "tui"
    assert msgs[1]["content"] == "hello world"
    # Assistant message should chain to the user message
    assert msgs[1]["parent_id"] == msgs[0]["id"]


def test_creates_session_if_missing(tmp_db: SessionDB) -> None:
    with patch.object(D, "_run_loop_blocking",
                      _stub_loop_returning("ok")):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="hi", agent_id="main",
                          source="wechat", peer_display="alice"),
        )
    sess = tmp_db.get_session("c1")
    assert sess is not None
    assert sess["agent_id"] == "main"
    assert sess["source"] == "wechat"
    assert sess["channel"] == "wechat"
    assert sess["peer_display"] == "alice"


def test_emits_chat_ack_then_stream_then_result(tmp_db, captured, collector) -> None:
    with patch.object(D, "_run_loop_blocking",
                      _stub_loop_returning("hello")):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="hi", agent_id="main", source="tui"),
            on_event=collector,
        )
    types = [(e["type"], e["data"].get("type")) for e in captured]
    assert types[0] == ("chat_ack", None)
    # Stream events from the stub
    assert ("chat_response", "stream_event") in types
    # Final result
    assert ("chat_response", "result") in types
    last = captured[-1]
    assert last["data"]["type"] == "result"
    assert last["data"]["content"] == "hello"


def test_updates_head_id_and_tokens(tmp_db) -> None:
    with patch.object(D, "_run_loop_blocking",
                      _stub_loop_returning("done", usage={"input_tokens": 42, "output_tokens": 7})):
        result = D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="hi", agent_id="main", source="tui"),
        )
    sess = tmp_db.get_session("c1")
    assert sess["head_id"] == result.assistant_msg_id
    assert sess["last_prompt_tokens"] == 42


def test_history_is_passed_to_loop(tmp_db) -> None:
    """Turn N+1 should see Turn N's user+assistant in history."""
    with patch.object(D, "_run_loop_blocking",
                      _stub_loop_returning("first reply")):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="hi", agent_id="main", source="tui"),
        )

    seen_history: list[list[dict]] = []
    def _capture_stub(*, req, history, on_event, cancel_event):
        seen_history.append(list(history))
        return "second reply", {"input_tokens": 0, "output_tokens": 0}, []

    with patch.object(D, "_run_loop_blocking", _capture_stub):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="follow-up", agent_id="main", source="tui"),
        )

    [history] = seen_history
    # Dispatcher passes prior history (user+assistant) WITHOUT the new
    # user message — agent_loop adds the prompt itself via
    # context.messages, so duplicating it here would break OpenAI
    # prompt cache.
    assert len(history) == 2
    assert history[0]["content"] == "hi"
    assert history[1]["content"] == "first reply"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_loop_exception_persisted_as_system_message(tmp_db, captured, collector) -> None:
    def _raise(**_):
        raise RuntimeError("boom")
    with patch.object(D, "_run_loop_blocking", _raise):
        result = D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="hi", agent_id="main", source="tui"),
            on_event=collector,
        )

    assert result.failed is True
    assert "boom" in (result.error or "")

    # User message still recorded; system error message appended.
    msgs = tmp_db.get_messages("c1")
    roles = [m["role"] for m in msgs]
    assert "user" in roles
    assert "system" in roles
    sys_msg = [m for m in msgs if m["role"] == "system"][-1]
    assert "boom" in sys_msg["content"]

    # Client got an error envelope (not a raw exception)
    err_events = [e for e in captured
                  if e["type"] == "chat_response"
                  and e["data"].get("type") == "error"]
    assert len(err_events) == 1


# ---------------------------------------------------------------------------
# Approval flow
# ---------------------------------------------------------------------------

def test_approval_bypass_skips_check(tmp_db, captured, collector) -> None:
    """permission_mode=bypass should never emit approval_request."""
    with patch.object(D, "_run_loop_blocking",
                      _stub_loop_returning("ok")):
        D.process_user_turn(
            D.TurnRequest(session_id="c1", user_text="hi", agent_id="main",
                          source="wechat", permission_mode="bypass"),
            on_event=collector,
        )
    assert not any(e["type"] == "approval_request" for e in captured)


def test_approval_registry_resolves(tmp_db) -> None:
    reg = D.approval_registry()
    rid = "test_req_1"
    waiter = reg.register(rid)
    # Resolve in another thread
    def _resolve():
        time.sleep(0.05)
        reg.resolve(rid, approved=True)
    threading.Thread(target=_resolve).start()
    assert waiter.wait(timeout=1.0) is True
    assert reg.consume(rid) is True


def test_approval_registry_unknown_id(tmp_db) -> None:
    reg = D.approval_registry()
    # Resolve before register: returns False
    assert reg.resolve("missing", approved=True) is False


# ---------------------------------------------------------------------------
# Approval flow integrated with dispatcher
# ---------------------------------------------------------------------------

def test_await_user_approval_emits_envelope_and_resolves(captured, collector) -> None:
    """``_await_user_approval`` must (a) post an approval_request envelope
    with the right shape and (b) unblock when the registry resolves the
    matching request_id. This is the low-level primitive the dispatcher's
    per-tool wrapper calls into when permission_mode is "ask"."""
    import asyncio

    req = D.TurnRequest(session_id="c1", user_text="hi", agent_id="main",
                        source="tui", permission_mode="ask")

    def _resolver():
        reg = D.approval_registry()
        for _ in range(50):
            time.sleep(0.02)
            with reg._lock:
                pending = list(reg._pending.keys())
            if pending:
                reg.resolve(pending[0], approved=True)
                return
    threading.Thread(target=_resolver, daemon=True).start()

    async def _drive() -> bool:
        return await D._await_user_approval(
            req=req, tool_name="bash", args={"command": "ls"},
            on_event=collector, timeout=5.0,
        )
    approved = asyncio.run(_drive())

    assert approved is True
    requests = [e for e in captured if e["type"] == "approval_request"]
    assert len(requests) == 1
    assert requests[0]["data"]["tool"] == "bash"
    assert requests[0]["data"]["args"] == {"command": "ls"}
    assert requests[0]["data"]["session_id"] == "c1"
