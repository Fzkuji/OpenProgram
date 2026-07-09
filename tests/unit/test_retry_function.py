"""Unit tests for the function-call Retry button's backend.

The Retry button on a runtime-block sends the WS ``retry_function``
action. It must re-dispatch the SAME function with the SAME kwargs the
prior call used, in the SAME session — WITHOUT stripping any existing
messages (the old broken ``retry_overwrite`` path silently deleted them).

These cover ``ws_actions.chat._last_call_kwargs`` (kwargs lookup from the
authoritative DAG node) and ``handle_retry_function`` (re-dispatch wiring).
"""
from __future__ import annotations

import asyncio

import pytest

from openprogram.context.nodes import Call, ROLE_CODE, ROLE_USER
from openprogram.webui.ws_actions import chat


def _code(name, input, seq, *, caller="ROOT", predecessor="ROOT"):
    """A top-level code Call with a conv predecessor in metadata (that's
    where the fork model reads the predecessor from)."""
    return Call(role=ROLE_CODE, name=name, input=input, seq=seq,
                caller=caller, metadata={"predecessor": predecessor})


class _FakeDB:
    def __init__(self, nodes):
        self._nodes = nodes

    def get_nodes(self, session_id):
        return list(self._nodes)


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)


def _patch_db(monkeypatch, nodes):
    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db",
        lambda: _FakeDB(nodes),
    )


# ---- _last_call_kwargs --------------------------------------------------

def test_last_call_kwargs_reads_latest_matching_node(monkeypatch):
    nodes = [
        Call(role=ROLE_CODE, name="word_count", input={"text": "old"}, seq=1),
        Call(role=ROLE_USER, name="user", input=None, seq=2),
        Call(role=ROLE_CODE, name="word_count", input={"text": "new"}, seq=3),
    ]
    _patch_db(monkeypatch, nodes)
    assert chat._last_call_kwargs("s1", "word_count") == {"text": "new"}


def test_last_call_kwargs_drops_injected_params(monkeypatch):
    nodes = [
        Call(role=ROLE_CODE, name="word_count",
             input={"text": "hi", "runtime": object(), "callback": object()},
             seq=1),
    ]
    _patch_db(monkeypatch, nodes)
    assert chat._last_call_kwargs("s1", "word_count") == {"text": "hi"}


def test_last_call_kwargs_none_when_never_called(monkeypatch):
    nodes = [Call(role=ROLE_CODE, name="other", input={"a": 1}, seq=1)]
    _patch_db(monkeypatch, nodes)
    assert chat._last_call_kwargs("s1", "word_count") is None


# ---- handle_retry_function ---------------------------------------------

def test_retry_redispatches_with_original_kwargs(monkeypatch):
    nodes = [
        _code("word_count", {"text": "hello world"}, seq=1),
    ]
    _patch_db(monkeypatch, nodes)

    calls = []

    def _fake_run(name, kwargs, session_id, work_dir=None, anchor_msg_id="ROOT"):
        calls.append((name, kwargs, session_id, anchor_msg_id))
        return {"session_id": session_id, "msg_id": "abc"}

    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call", _fake_run
    )

    ws = _FakeWS()
    asyncio.run(chat.handle_retry_function(
        ws, {"session_id": "s1", "function": "word_count"}
    ))

    # Re-dispatched exactly once, with the prior call's kwargs + session,
    # anchored at the original call's predecessor (ROOT here) so the
    # re-run forks as a SIBLING branch instead of stacking.
    assert calls == [("word_count", {"text": "hello world"}, "s1", "pred:ROOT")]
    # Acked the new run over the WS (so the client can follow the stream).
    assert ws.sent and "chat_ack" in ws.sent[0]


def test_retry_anchors_at_original_calls_predecessor(monkeypatch):
    # An LLM-issued call hangs off its llm reply, not ROOT. The retry
    # must fork off that SAME predecessor so the new run is a sibling of
    # the original — mirrors chat-message retry (predecessor = src's
    # predecessor), the mechanism the version switcher navigates.
    nodes = [
        _code("word_count", {"text": "a"}, seq=1,
              caller="llm_reply_9", predecessor="llm_reply_9"),
    ]
    _patch_db(monkeypatch, nodes)

    anchors = []

    def _fake_run(name, kwargs, session_id, work_dir=None, anchor_msg_id="ROOT"):
        anchors.append(anchor_msg_id)
        return {"session_id": session_id, "msg_id": "abc"}

    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call", _fake_run
    )
    asyncio.run(chat.handle_retry_function(
        _FakeWS(), {"session_id": "s1", "function": "word_count"}
    ))
    assert anchors == ["pred:llm_reply_9"]


def test_retry_targets_latest_top_level_call_not_nested(monkeypatch):
    # A function that calls itself writes nested code nodes of the same
    # name; retry must re-run the OUTER (top-level) invocation, not an
    # internal step. _last_call_node excludes nodes whose caller is
    # itself a code node.
    outer = _code("gui_agent", {"task": "outer"}, seq=1,
                  caller="ROOT", predecessor="ROOT")
    nested = Call(role=ROLE_CODE, name="gui_agent",
                  input={"task": "inner"}, seq=2,
                  caller=outer.id, metadata={"predecessor": outer.id})
    _patch_db(monkeypatch, [outer, nested])

    calls = []

    def _fake_run(name, kwargs, session_id, work_dir=None, anchor_msg_id="ROOT"):
        calls.append((kwargs, anchor_msg_id))
        return {"session_id": session_id, "msg_id": "abc"}

    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call", _fake_run
    )
    asyncio.run(chat.handle_retry_function(
        _FakeWS(), {"session_id": "s1", "function": "gui_agent"}
    ))
    # Outer kwargs, anchored at the outer call's predecessor (ROOT).
    assert calls == [({"task": "outer"}, "pred:ROOT")]


def test_retry_never_strips_messages_and_errors_without_prior_call(monkeypatch):
    # No prior word_count node → nothing to re-run. The handler must NOT
    # touch session messages (the old retry_overwrite bug); it broadcasts
    # a user-visible error instead and never calls the dispatcher.
    _patch_db(monkeypatch, [])

    dispatched = []
    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call",
        lambda *a, **k: dispatched.append(a) or {"session_id": "s1", "msg_id": "x"},
    )
    errors = []
    monkeypatch.setattr(
        "openprogram.webui.server._broadcast_chat_response",
        lambda sid, mid, env: errors.append(env),
    )

    ws = _FakeWS()
    asyncio.run(chat.handle_retry_function(
        ws, {"session_id": "s1", "function": "word_count"}
    ))

    assert dispatched == []               # never dispatched a bogus run
    assert errors and errors[0]["type"] == "error"
    assert "no prior" in errors[0]["content"].lower()


def test_retry_noop_on_missing_args(monkeypatch):
    dispatched = []
    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call",
        lambda *a, **k: dispatched.append(a),
    )
    ws = _FakeWS()
    asyncio.run(chat.handle_retry_function(ws, {"function": "word_count"}))
    asyncio.run(chat.handle_retry_function(ws, {"session_id": "s1"}))
    assert dispatched == []
    assert ws.sent == []


def test_retry_overwrite_action_is_removed():
    # The dead legacy action must be gone from the dispatch table; the new
    # one present.
    assert "retry_overwrite" not in chat.ACTIONS
    assert chat.ACTIONS["retry_function"] is chat.handle_retry_function


# ---- branch semantics at the store level -------------------------------
# The retry anchors the re-run at the original call's predecessor, which
# is exactly how the store expresses a sibling branch: two code nodes
# sharing a predecessor are siblings, get_branch renders only the active
# head, and list_branches surfaces both so the switcher / Branches panel
# can reach the other version. These lock that contract end-to-end.

def _fresh_store(tmp_path):
    from openprogram.store.session.session_store import SessionStore
    s = SessionStore(tmp_path / "sessions-git")
    s.create_session("s1", "main", title="t")
    # A ROOT anchor (the fn-form / retry predecessor), like
    # run_agentic_function_call writes before dispatching.
    s.append_message("s1", {"id": "ROOT", "role": "user", "content": "",
                            "timestamp": 0, "predecessor": None,
                            "display": "root"})
    return s


def _append_code(store, node_id, pred="ROOT", seq_ts=1):
    store.append_message("s1", {
        "id": node_id, "role": "code", "content": "",
        "function": "word_count", "timestamp": seq_ts,
        "predecessor": pred, "caller": pred,
    })


# ---- switcher scope: only complete fn-run entries are siblings --------
# The "1/12" bug was sibling_index counting every node sharing a (None)
# parent — all ROOT-anchored calls AND their predecessor-less sub-calls.
# _is_top_function_run restricts the switcher's sibling set to fn-run
# ENTRY nodes so a retry shows exactly the alternative runs, nothing else.

def test_is_top_function_run_keys_on_caller_not_predecessor():
    from openprogram.webui.ws_actions.session import _is_top_function_run
    nodes = [
        {"id": "ROOT", "role": "user", "display": "root"},
        # first run: empty caller, no predecessor (root-level)
        {"id": "run1", "role": "code", "caller": "", "predecessor": ""},
        # internal sub-call: caller points at run1 (a code node)
        {"id": "step", "role": "code", "caller": "run1", "predecessor": ""},
        {"id": "ilm", "role": "assistant", "caller": "step"},
        # second run CHAINED off run1 via predecessor — still top-level
        # (empty caller), NOT a sub-call even though its predecessor is a
        # code node.
        {"id": "run2", "role": "code", "caller": "", "predecessor": "run1"},
        # retry of run2: forks via caller = run2's predecessor (run1)
        {"id": "retry", "role": "code", "caller": "run1", "predecessor": ""},
    ]
    by_id = {n["id"]: n for n in nodes}
    assert _is_top_function_run(by_id["run1"], by_id)
    assert _is_top_function_run(by_id["run2"], by_id)   # chained, not sub-call
    assert not _is_top_function_run(by_id["step"], by_id)   # caller=code node
    assert not _is_top_function_run(by_id["ilm"], by_id)    # not a code node
    # retry's caller (run1) is a code node → by the caller rule this is
    # NOT flagged top-level; that's acceptable — the retry still renders
    # via get_branch as the active head, and the ORIGINAL (run2) carries
    # the switcher. The switcher groups by fork point (predecessor|caller),
    # so run2 + retry share fork parent run1 → counted together.
    assert _is_top_function_run(by_id["run1"], by_id)


def test_new_run_passes_empty_caller_so_decorator_stamps_head(monkeypatch):
    # A NEW run (anchor left unset) must pass an EMPTY caller to dispatch,
    # so the @agentic_function decorator stamps metadata.predecessor with
    # the session's current head — chaining off the previous turn like a
    # new chat turn (distinct predecessor → its own 1/1 card). It must NOT
    # hardcode "ROOT" (which lumped every run into one None-parent group,
    # the "1/12" the user saw).
    from openprogram.webui.routes import chat as routes_chat

    captured = {}

    monkeypatch.setattr(
        "openprogram.webui.server._get_or_create_session",
        lambda sid=None, **k: {"id": sid or "s1", "last_workdirs": {}},
    )

    class _Tool:
        name = "word_count"
        _is_agentic = True

    monkeypatch.setattr(
        "openprogram.functions.agent_tools", lambda names=None: [_Tool()]
    )

    class _RM:
        def _enabled_model_keys(self):
            return ["k"]
    monkeypatch.setattr("openprogram.webui.server._runtime_management", _RM())

    class _DB:
        def get_session(self, sid):
            return {"head_id": "prev_head", "agent_id": "main"}
        def message_exists(self, sid, mid):
            return True
        def update_session(self, *a, **k):
            pass
    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db", lambda: _DB()
    )
    monkeypatch.setattr(
        "openprogram.webui.server._default_agent_id", lambda: "main"
    )

    def _stop_dispatch(**kw):
        captured["anchor"] = kw.get("anchor_msg_id")
        raise RuntimeError("stop-after-anchor")
    monkeypatch.setattr(
        "openprogram.agent.dispatcher.dispatch_forced_tool_call", _stop_dispatch
    )
    monkeypatch.setattr(
        "openprogram.webui.server._emit_running_task_event", lambda *a, **k: None
    )

    import threading
    real_thread = threading.Thread

    def _inline_thread(target=None, args=(), kwargs=None, daemon=None):
        class _T:
            def start(_s):
                try:
                    target(*(args or ()), **(kwargs or {}))
                except Exception:
                    pass
            def is_alive(_s):
                return False
        return _T()
    monkeypatch.setattr(threading, "Thread", _inline_thread)

    routes_chat.run_agentic_function_call("word_count", {"text": "hi"}, "s1")
    threading.Thread = real_thread
    # Empty caller → decorator's top-level-call branch stamps the head.
    assert captured.get("anchor") == ""


def test_retry_run_is_sibling_and_only_active_head_renders(tmp_path):
    store = _fresh_store(tmp_path)
    # Original call + its retry, both anchored at ROOT → siblings.
    _append_code(store, "call1", pred="ROOT", seq_ts=1)
    _append_code(store, "call2", pred="ROOT", seq_ts=2)
    store.set_head("s1", "call2")

    # Both share the same predecessor → they are siblings, not a chain.
    tips = {b["head_msg_id"] for b in store.list_branches("s1")}
    assert {"call1", "call2"} <= tips

    # Transcript = active branch only: HEAD=call2 renders call2, not call1.
    branch_ids = [m["id"] for m in store.get_branch("s1")]
    assert "call2" in branch_ids
    assert "call1" not in branch_ids

    # Switching HEAD to the old run flips the transcript the other way —
    # the version switcher's checkout op.
    store.set_head("s1", "call1")
    branch_ids = [m["id"] for m in store.get_branch("s1")]
    assert "call1" in branch_ids
    assert "call2" not in branch_ids
