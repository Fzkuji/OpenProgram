"""Unit tests for the function-call Retry button's backend.

The Retry button on a runtime-block sends the WS ``retry_function``
action. It must re-dispatch the SAME function with the SAME kwargs the
prior call used, in the SAME session — WITHOUT stripping any existing
messages (the old broken ``retry_overwrite`` path silently deleted them).

These cover exact authoritative DAG-node lookup and
``handle_retry_function`` re-dispatch wiring.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace

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


# ---- exact call lookup --------------------------------------------------

@pytest.mark.parametrize("bad_node", [
    Call(role=ROLE_CODE, name="other", input={"text": "hi"}, seq=1),
    Call(role=ROLE_CODE, name="word_count", input=None, seq=1),
    Call(role=ROLE_USER, name="word_count", input={"text": "hi"}, seq=1),
])
def test_retry_call_node_validates_role_name_and_input(monkeypatch, bad_node):
    _patch_db(monkeypatch, [bad_node])
    assert chat._retry_call_node(
        "s1", bad_node.id, "word_count",
    ) is None


def test_retry_call_node_rejects_nested_call(monkeypatch):
    outer = _code("word_count", {"text": "outer"}, seq=1)
    nested = Call(
        role=ROLE_CODE,
        name="word_count",
        input={"text": "inner"},
        seq=2,
        caller=outer.id,
    )
    _patch_db(monkeypatch, [outer, nested])
    assert chat._retry_call_node("s1", nested.id, "word_count") is None
    assert chat._retry_call_node("s1", outer.id, "word_count") is outer


# ---- handle_retry_function ---------------------------------------------

def test_retry_redispatches_with_original_kwargs(monkeypatch):
    nodes = [
        _code("word_count", {"text": "hello world"}, seq=1),
    ]
    _patch_db(monkeypatch, nodes)

    calls = []

    def _fake_run(name, kwargs, session_id, anchor_msg_id="ROOT"):
        calls.append((name, kwargs, session_id, anchor_msg_id))
        return {"session_id": session_id, "msg_id": "abc"}

    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call", _fake_run
    )

    ws = _FakeWS()
    asyncio.run(chat.handle_retry_function(
        ws, {
            "session_id": "s1",
            "function": "word_count",
            "node_id": nodes[0].id,
        }
    ))

    # Re-dispatched exactly once, with the prior call's kwargs + session,
    # anchored at the original call's predecessor (ROOT here) so the
    # re-run forks as a SIBLING branch instead of stacking.
    assert calls == [("word_count", {"text": "hello world"}, "s1", "pred:ROOT")]
    # Acked the new run over the WS (so the client can follow the stream).
    assert ws.sent and "chat_ack" in ws.sent[0]


def test_retry_targets_clicked_node_and_acks_canonical_execution(monkeypatch):
    older = _code("gui_agent", {"task": "older"}, seq=1)
    newer = _code("gui_agent", {"task": "newer"}, seq=2)
    _patch_db(monkeypatch, [older, newer])
    calls = []

    def _fake_run(name, kwargs, session_id, anchor_msg_id="ROOT"):
        calls.append((name, kwargs, session_id, anchor_msg_id))
        return {
            "session_id": session_id,
            "msg_id": "transport-message",
            "execution_id": "canonical-code-node",
        }

    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call", _fake_run,
    )
    ws = _FakeWS()
    asyncio.run(chat.handle_retry_function(ws, {
        "session_id": "s1",
        "function": "gui_agent",
        "node_id": older.id,
    }))

    assert calls == [
        ("gui_agent", {"task": "older"}, "s1", "pred:ROOT"),
    ]
    ack = json.loads(ws.sent[0])
    assert ack["data"]["execution_id"] == "canonical-code-node"
    assert ack["data"]["msg_id"] == "transport-message"


def test_retry_requires_exact_node_id_instead_of_latest_name(monkeypatch):
    node = _code("word_count", {"text": "latest"}, seq=1)
    _patch_db(monkeypatch, [node])
    dispatched = []
    errors = []
    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "openprogram.webui.server._broadcast_chat_response",
        lambda sid, mid, env: errors.append(env),
    )

    asyncio.run(chat.handle_retry_function(_FakeWS(), {
        "session_id": "s1",
        "function": "word_count",
    }))

    assert dispatched == []
    assert errors and "call node" in errors[0]["content"].lower()


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

    def _fake_run(name, kwargs, session_id, anchor_msg_id="ROOT"):
        anchors.append(anchor_msg_id)
        return {"session_id": session_id, "msg_id": "abc"}

    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call", _fake_run
    )
    asyncio.run(chat.handle_retry_function(
        _FakeWS(), {
            "session_id": "s1",
            "function": "word_count",
            "node_id": nodes[0].id,
        }
    ))
    assert anchors == ["pred:llm_reply_9"]


def test_retry_targets_exact_top_level_call_not_nested(monkeypatch):
    # A function that calls itself writes nested code nodes of the same
    # name; the exact outer card may be retried, but its internal step may
    # not be used as a Retry target.
    outer = _code("gui_agent", {"task": "outer"}, seq=1,
                  caller="ROOT", predecessor="ROOT")
    nested = Call(role=ROLE_CODE, name="gui_agent",
                  input={"task": "inner"}, seq=2,
                  caller=outer.id, metadata={"predecessor": outer.id})
    _patch_db(monkeypatch, [outer, nested])

    calls = []

    def _fake_run(name, kwargs, session_id, anchor_msg_id="ROOT"):
        calls.append((kwargs, anchor_msg_id))
        return {"session_id": session_id, "msg_id": "abc"}

    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call", _fake_run
    )
    asyncio.run(chat.handle_retry_function(
        _FakeWS(), {
            "session_id": "s1",
            "function": "gui_agent",
            "node_id": outer.id,
        }
    ))
    # Outer kwargs, anchored at the outer call's predecessor (ROOT).
    assert calls == [({"task": "outer"}, "pred:ROOT")]


def test_retry_preserves_registered_origin_window(monkeypatch):
    from openprogram.webui.ws_actions import webtab

    node = _code("gui_agent", {"task": "inspect"}, seq=1)
    _patch_db(monkeypatch, [node])
    seen = {}
    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call",
        lambda *args, **kwargs: seen.update(kwargs) or {
            "session_id": args[2], "msg_id": "abc",
        },
    )
    ws = _FakeWS()
    asyncio.run(webtab.handle_webtab_register(ws, {
        "action": "webtab_register", "window_id": "window-2",
    }))
    try:
        asyncio.run(chat.handle_retry_function(
            ws, {
                "session_id": "s1",
                "function": "gui_agent",
                "node_id": node.id,
            },
        ))
        assert seen["origin_window_id"] == "window-2"
        assert "surface_ref" not in seen
    finally:
        webtab.release_connection(ws)


def test_retry_legacy_node_uses_click_time_origin_page(monkeypatch):
    from openprogram.webui.ws_actions import webtab

    node = _code("gui_agent", {"task": "inspect"}, seq=1)
    _patch_db(monkeypatch, [node])
    seen = {}
    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call",
        lambda *args, **kwargs: seen.update(kwargs) or {
            "session_id": args[2], "msg_id": "abc",
        },
    )
    ws = _FakeWS()
    asyncio.run(webtab.handle_webtab_register(ws, {
        "action": "webtab_register", "window_id": "window-2",
    }))
    try:
        asyncio.run(chat.handle_retry_function(
            ws,
            {
                "session_id": "s1",
                "function": "gui_agent",
                "node_id": node.id,
                "surface_ref": {
                    "window_id": "window-2",
                    "tab_id": "page-exact",
                },
            },
        ))
        assert seen["origin_window_id"] == "window-2"
        assert seen["surface_ref"] == {
            "window_id": "window-2",
            "tab_id": "page-exact",
        }
    finally:
        webtab.release_connection(ws)


@pytest.mark.parametrize(
    ("stored_tab", "expected_surface"),
    [
        (
            "page-original",
            {
                "version": 1,
                "window_id": "window-2",
                "tab_id": "page-original",
            },
        ),
        (None, None),
    ],
)
def test_retry_uses_versioned_persisted_origin_not_current_page(
    monkeypatch, stored_tab, expected_surface,
):
    from openprogram.webui.ws_actions import webtab

    node = _code("gui_agent", {"task": "inspect"}, seq=1)
    node.metadata["surface_origin"] = {
        "version": 1,
        "window_id": "window-2",
    }
    if stored_tab:
        node.metadata["surface_origin"]["tab_id"] = stored_tab
    _patch_db(monkeypatch, [node])
    seen = {}
    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call",
        lambda *args, **kwargs: seen.update(kwargs) or {
            "session_id": args[2], "msg_id": "transport",
            "execution_id": "execution",
        },
    )
    ws = _FakeWS()
    asyncio.run(webtab.handle_webtab_register(ws, {
        "action": "webtab_register", "window_id": "window-2",
    }))
    try:
        asyncio.run(chat.handle_retry_function(ws, {
            "session_id": "s1",
            "function": "gui_agent",
            "node_id": node.id,
            "surface_ref": {
                "window_id": "window-2",
                "tab_id": "page-current",
            },
        }))
        assert seen["origin_window_id"] == "window-2"
        if expected_surface is None:
            assert "surface_ref" not in seen
        else:
            assert seen["surface_ref"] == expected_surface
    finally:
        webtab.release_connection(ws)


@pytest.mark.parametrize(
    ("stored_origin", "registered_window", "error_fragment"),
    [
        (
            {
                "version": 1,
                "window_id": "window-original",
                "tab_id": "page-original",
            },
            "window-current",
            "original desktop window",
        ),
        (
            {"version": 2, "window_id": "window-current"},
            "window-current",
            "invalid stored Page origin",
        ),
    ],
)
def test_retry_rejects_invalid_or_disconnected_persisted_origin(
    monkeypatch, stored_origin, registered_window, error_fragment,
):
    from openprogram.webui.ws_actions import webtab

    node = _code("gui_agent", {"task": "inspect"}, seq=1)
    node.metadata["surface_origin"] = stored_origin
    _patch_db(monkeypatch, [node])
    dispatched = []
    errors = []
    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "openprogram.webui.server._broadcast_chat_response",
        lambda sid, mid, env: errors.append(env),
    )
    ws = _FakeWS()
    asyncio.run(webtab.handle_webtab_register(ws, {
        "action": "webtab_register", "window_id": registered_window,
    }))
    try:
        asyncio.run(chat.handle_retry_function(ws, {
            "session_id": "s1",
            "function": "gui_agent",
            "node_id": node.id,
            "surface_ref": {
                "window_id": "window-current",
                "tab_id": "page-current",
            },
        }))
        assert dispatched == []
        assert errors and error_fragment in errors[0]["content"]
    finally:
        webtab.release_connection(ws)


def test_retry_rejects_surface_from_another_window(monkeypatch):
    from openprogram.webui.ws_actions import webtab

    node = _code("gui_agent", {"task": "inspect"}, seq=1)
    _patch_db(monkeypatch, [node])
    dispatched = []
    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call",
        lambda *args, **kwargs: dispatched.append((args, kwargs)) or {
            "session_id": args[2], "msg_id": "abc",
        },
    )
    errors = []
    monkeypatch.setattr(
        "openprogram.webui.server._broadcast_chat_response",
        lambda sid, mid, env: errors.append(env),
    )
    ws = _FakeWS()
    asyncio.run(webtab.handle_webtab_register(ws, {
        "action": "webtab_register", "window_id": "window-2",
    }))
    try:
        asyncio.run(chat.handle_retry_function(
            ws,
            {
                "session_id": "s1",
                "function": "gui_agent",
                "node_id": node.id,
                "surface_ref": {
                    "window_id": "window-other",
                    "tab_id": "page-other",
                },
            },
        ))
        assert dispatched == []
        assert errors and "another desktop window" in errors[0]["content"]
    finally:
        webtab.release_connection(ws)


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
        ws, {
            "session_id": "s1",
            "function": "word_count",
            "node_id": "missing-node",
        }
    ))

    assert dispatched == []               # never dispatched a bogus run
    assert errors and errors[0]["type"] == "error"
    assert "call node" in errors[0]["content"].lower()


def test_retry_noop_on_missing_args(monkeypatch):
    dispatched = []
    monkeypatch.setattr(
        "openprogram.webui.routes.chat.run_agentic_function_call",
        lambda *a, **k: dispatched.append(a),
    )
    ws = _FakeWS()
    asyncio.run(chat.handle_retry_function(ws, {
        "function": "word_count", "node_id": "node-1",
    }))
    asyncio.run(chat.handle_retry_function(ws, {
        "session_id": "s1", "node_id": "node-1",
    }))
    assert dispatched == []
    assert ws.sent == []


def test_retry_overwrite_action_is_removed():
    # The dead legacy action must be gone from the dispatch table; the new
    # one present.
    assert "retry_overwrite" not in chat.ACTIONS
    assert chat.ACTIONS["retry_function"] is chat.handle_retry_function


def test_switch_attempt_action_and_handler_removed():
    # The pre-rewrite in-memory ``attempts`` model (switch_attempt WS action
    # + handle_switch_attempt) is dead — the version switcher navigates DAG
    # siblings by HEAD checkout now. Both must be gone so two version models
    # don't half-coexist.
    assert "switch_attempt" not in chat.ACTIONS
    assert not hasattr(chat, "handle_switch_attempt")


# ---- concurrency guard: no second run while one is in flight ----------

def test_new_run_rejected_while_run_active(monkeypatch):
    # A fn-form / Retry run must be refused with 409 when a run is already
    # in flight in the same session — otherwise two runs advance HEAD
    # concurrently and interleave the conversation chain. Mirrors the
    # chat-retry path's _is_run_active guard.
    from openprogram.webui.routes import chat as routes_chat

    monkeypatch.setattr(
        "openprogram.webui.server._get_or_create_session",
        lambda sid=None, **k: {"id": sid or "s1"},
    )
    class _Tool:
        name = "word_count"
        _is_agentic = True

    monkeypatch.setattr(
        "openprogram.programs.agent_tools", lambda names=None: [_Tool()]
    )
    monkeypatch.setattr(
        "openprogram.programs._runtime.get",
        lambda name, *a, **k: _Tool() if name == _Tool.name else None,
    )

    class _RM:
        def _enabled_model_keys(self):
            return ["k"]
    monkeypatch.setattr("openprogram.webui.server._runtime_management", _RM())

    # The session already has an in-flight run.
    monkeypatch.setattr(
        "openprogram.webui.server._is_run_active", lambda sid: True
    )

    dispatched = []
    monkeypatch.setattr(
        "openprogram.agent.dispatcher.dispatch_forced_tool_call",
        lambda **kw: dispatched.append(kw),
    )

    result = routes_chat.run_agentic_function_call("word_count", {"text": "hi"}, "s1")
    assert result.get("status_code") == 409
    assert result.get("code") == "run_active"
    # Never spawned a competing dispatch.
    assert dispatched == []


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
        lambda sid=None, **k: {"id": sid or "s1"},
    )
    monkeypatch.setattr(
        "openprogram.webui.server._is_run_active", lambda _sid: False
    )

    class _Tool:
        name = "word_count"
        _is_agentic = True

    monkeypatch.setattr(
        "openprogram.programs.agent_tools", lambda names=None: [_Tool()]
    )
    monkeypatch.setattr(
        "openprogram.programs._runtime.get",
        lambda name, *a, **k: _Tool() if name == _Tool.name else None,
    )

    class _RM:
        def _enabled_model_keys(self):
            return ["k"]

        def _resolve_session_provider_model(self, conv):
            return "minimax-cn-coding-plan", "MiniMax-M3"
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

    from openprogram.agent import production_driver
    from openprogram.execution.model import ExecutionStatus
    real_adapter = production_driver.CanonicalAgentAdapter

    class _Adapter:
        def __init__(self, *args, **kwargs):
            self._real = real_adapter(*args, **kwargs)

        def admit_payload(self, **kwargs):
            captured.update(kwargs["payload"])
            return self._real.admit_payload(**kwargs)

        async def activate(self, admission, *, on_activated=None):
            service = self._real.driver._control_service()
            attempt, leased = service.attempts.lease(
                admission.execution_id,
                expected_version=admission.status_version,
                owner_id="unit-test",
                ttl_seconds=30,
            )
            active, running = service.attempts.activate(
                attempt.attempt_id,
                generation=attempt.generation,
                expected_execution_version=leased.status_version,
            )
            service.finish_attempt(
                attempt_id=active.attempt_id,
                generation=active.generation,
                expected_execution_version=running.status_version,
                target=ExecutionStatus.COMPLETED,
                outcome="completed",
            )
            activation = SimpleNamespace(
                admission=admission, status_version=running.status_version,
            )
            if on_activated is not None:
                on_activated(activation)
            return activation, SimpleNamespace(failed=False, error=None)

        def fail_admission(self, *args, **kwargs):
            return self._real.fail_admission(*args, **kwargs)

    monkeypatch.setattr(production_driver, "CanonicalAgentAdapter", _Adapter)
    monkeypatch.setattr(
        "openprogram.agentic_programming.function.create_pending_call_node",
        lambda **k: None,
    )
    monkeypatch.setattr(
        "openprogram.webui.server._emit_running_task_event", lambda *a, **k: None
    )

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
    monkeypatch.setattr(
        routes_chat, "threading", SimpleNamespace(Thread=_inline_thread)
    )

    routes_chat.run_agentic_function_call("word_count", {"text": "hi"}, "s1")
    # Empty caller → decorator's top-level-call branch stamps the head.
    assert captured.get("anchor_msg_id") == ""


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


def test_fn_run_siblings_sort_once_with_stable_source_positions():
    from openprogram.webui.ws_actions import session as ws_session

    messages = [
        {"id": "internal", "role": "code", "predecessor": "p", "created_at": 1},
        {"id": "run-late", "role": "code", "predecessor": "p", "created_at": 2},
        {"id": "tool", "role": "tool", "predecessor": "p", "created_at": 0},
        {"id": "run-early", "role": "code", "predecessor": "p", "created_at": 1},
        {"id": "other", "role": "code", "predecessor": "other-p", "created_at": 0},
        {"id": "run-tied", "role": "code", "predecessor": "p", "created_at": 1},
    ]
    by_id = {message["id"]: message for message in messages}
    siblings_by_pred = {"p": [
        (message, position)
        for position, message in enumerate(messages)
        if message["id"] in {"run-late", "run-early", "run-tied"}
    ]}
    ordered = ws_session._ordered_fn_run_siblings(
        by_id,
        siblings_by_pred,
        "run-late",
        lambda message: message.get("predecessor"),
    )

    assert ordered == ["run-early", "run-tied", "run-late"]
    assert "other" not in ordered
    assert "internal" not in ordered
    assert "tool" not in ordered


@pytest.mark.parametrize("message_id", ["unknown", None])
def test_fn_run_siblings_handles_unknown_and_malformed_ids(message_id):
    from openprogram.webui.ws_actions import session as ws_session

    messages = [
        {"id": "duplicate", "role": "code", "predecessor": None},
        {"id": "duplicate", "role": "code", "predecessor": None},
        {"role": "code", "predecessor": None},
        object(),
    ]
    by_id = {"duplicate": messages[1], None: messages[2]}
    siblings_by_pred = {None: [
        (message, position)
        for position, message in enumerate(messages)
        if isinstance(message, dict)
    ]}
    result = ws_session._ordered_fn_run_siblings(
        by_id,
        siblings_by_pred,
        message_id,
        lambda message: message.get("predecessor"),
    )

    if message_id == "unknown":
        assert result == []
    else:
        assert result == ["duplicate", "duplicate", None]


def test_fn_run_siblings_does_not_call_list_index():
    from openprogram.webui.ws_actions import session as ws_session

    source = inspect.getsource(ws_session.handle_load_session)
    assert "all_msgs.index" not in source

    class IndexForbiddenList(list):
        def index(self, value, *args):
            raise AssertionError("sibling sorting must not scan with list.index")

    messages = IndexForbiddenList([
        {"id": "first", "role": "code", "predecessor": "p", "created_at": 1},
        {"id": "second", "role": "code", "predecessor": "p", "created_at": 1},
    ])
    siblings_by_pred = {"p": [
        (message, position) for position, message in enumerate(messages)
    ]}
    expected = ["first", "second"]
    first = ws_session._ordered_fn_run_siblings(
        {message["id"]: message for message in messages},
        siblings_by_pred,
        "second",
        lambda message: message.get("predecessor"),
    )
    second = ws_session._ordered_fn_run_siblings(
        {message["id"]: message for message in messages},
        siblings_by_pred,
        "first",
        lambda message: message.get("predecessor"),
    )
    assert first == second == expected
