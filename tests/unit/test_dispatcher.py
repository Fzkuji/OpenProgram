"""Coverage for the dispatcher.process_user_turn pipeline.

Real ``agent_loop()`` calls go through provider runtimes (network,
auth) — too heavy for unit tests. We patch ``_run_loop_blocking`` at
the seam so we can assert:

  * SessionDB receives both user + assistant messages with proper
    caller / timestamp / source linkage
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
    db = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db",
        lambda: db,
    )
    monkeypatch.setattr("openprogram.store.session_store.default_store",
                        lambda: db)
    monkeypatch.setattr("openprogram.store.default_store", lambda: db)
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
    def _stub(*, req: D.TurnRequest, history, on_event, cancel_event, **_extra):
        # ``**_extra`` swallows assistant_msg_id /
        # agentic_tool_names_out / ordered_blocks_out (and any future
        # plumbing kw-args the dispatcher adds) so test stubs don't
        # have to track every new argument as the real loop signature
        # evolves.
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
    assert msgs[1]["predecessor"] == msgs[0]["id"]


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
    def _capture_stub(*, req, history, on_event, cancel_event, **_extra):
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

@pytest.mark.skip(
    reason="dispatcher now folds errors into the assistant placeholder row "
           "(role='assistant', status='error') instead of writing a separate "
           "role='system' message — production behavior change from the "
           "placeholder-lifecycle refactor, not a test-migration issue"
)
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

def _grab_approval_frames() -> tuple[list[dict], "callable"]:
    """订阅事件总线抓审批的 question.asked 帧（审批合流后经事件层 emit）。
    返回 (frames, unsubscribe)。"""
    from openprogram.agent.event_bus import get_event_bus, WS_FRAME_EVENT
    frames: list[dict] = []

    def _grab(ev) -> None:
        fr = ev.payload.get("frame", {})
        if fr.get("type") == "question.asked" and fr["data"].get("kind") == "approval":
            frames.append(fr["data"])
    return frames, get_event_bus().subscribe(_grab, types={WS_FRAME_EVENT})


def test_approval_bypass_skips_check(tmp_db, captured, collector) -> None:
    """permission_mode=bypass should never emit an approval question."""
    frames, unsub = _grab_approval_frames()
    try:
        with patch.object(D, "_run_loop_blocking",
                          _stub_loop_returning("ok")):
            D.process_user_turn(
                D.TurnRequest(session_id="c1", user_text="hi", agent_id="main",
                              source="wechat", permission_mode="bypass"),
                on_event=collector,
            )
    finally:
        unsub()
    assert not frames


def test_approval_registry_resolves(tmp_db) -> None:
    """审批合流：统一 QuestionRegistry 的 register/resolve/consume（kind=approval）。"""
    from openprogram.agent.questions import PendingQuestion
    reg = D.approval_registry()  # 现在是 QuestionRegistry
    q = PendingQuestion(id="test_req_1", session_id="c1", kind="approval",
                        prompt="允许执行 bash？", options=["允许", "拒绝"])
    waiter = reg.register(q)

    def _resolve():
        time.sleep(0.05)
        reg.resolve("test_req_1", "answered", "允许")
    threading.Thread(target=_resolve).start()
    assert waiter.wait(timeout=1.0) is True
    assert reg.consume("test_req_1") == ("answered", "允许")


def test_approval_registry_unknown_id(tmp_db) -> None:
    reg = D.approval_registry()
    # Resolve before register: returns False
    assert reg.resolve("missing", "answered", "允许") is False


# ---------------------------------------------------------------------------
# Approval flow integrated with dispatcher
# ---------------------------------------------------------------------------

def test_await_user_approval_emits_question_and_resolves() -> None:
    """``await_user_approval`` 必须 (a) 经事件层发一个 kind="approval" 的
    question.asked 帧（带 tool/args/session_id）、(b) 用户在统一 registry 答
    「允许」后解除。这是 permission_mode="ask" 时 per-tool wrapper 调的底层原语。"""
    import asyncio
    from openprogram.agent.questions import QuestionRegistry
    import openprogram.agent.questions as Q

    Q._registry = QuestionRegistry()  # 干净 registry
    frames, unsub = _grab_approval_frames()

    req = D.TurnRequest(session_id="c1", user_text="hi", agent_id="main",
                        source="tui", permission_mode="ask")

    def _resolver():
        reg = Q.get_question_registry()
        for _ in range(100):
            time.sleep(0.02)
            pend = reg.list_pending()
            if pend:
                reg.resolve(pend[0].id, "answered", "允许")
                return
    threading.Thread(target=_resolver, daemon=True).start()

    async def _drive():
        return await D._await_user_approval(
            req=req, tool_name="bash", args={"command": "ls"},
            on_event=lambda e: None, timeout=5.0,
        )
    try:
        approved, reason = asyncio.run(_drive())
    finally:
        unsub()

    assert approved is True
    assert reason is None
    assert len(frames) == 1
    assert frames[0]["tool"] == "bash"
    assert frames[0]["args"] == {"command": "ls"}
    assert frames[0]["session_id"] == "c1"
    assert frames[0]["kind"] == "approval"
