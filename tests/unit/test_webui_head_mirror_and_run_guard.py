"""Webui head-mirror and run-guard regressions.

Four related defects, one root pattern: the store HEAD and the webui's
in-memory mirror (``_sessions[sid]["head_id"]`` / ``["messages"]``) must
move together, and turn entry points must not race a run in flight.

1. ``/merge`` (slash path, ``_execute._run_merge``): process_user_turn
   advanced the store HEAD but the mirror stayed pre-merge, so the
   ``_save_session`` right after execution flushed the old head back
   and orphaned the merge reply.
2. WS ``merge_branches``: same story, different entry point.
3. ``rewind``: the handler only synced the mirror when ``errors`` was
   empty — but ``rewind_to`` moves the head unconditionally, so a
   file-restore failure left mirror and store split.
4. WS ``chat``: the only turn entry point with no ``_is_run_active``
   guard; two racing clients could advance the same HEAD concurrently.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


@pytest.fixture
def server_events(monkeypatch):
    """Record _set_active_head calls and broadcasts in arrival order."""
    from openprogram.webui import server as _s
    events: list[tuple] = []
    monkeypatch.setattr(
        _s, "_set_active_head",
        lambda sid, head: events.append(("head", sid, head)),
    )
    monkeypatch.setattr(
        _s, "_broadcast",
        lambda msg: events.append(("broadcast", json.loads(msg).get("type"))),
    )
    monkeypatch.setattr(_s, "refresh_context_stats", lambda sid: None)
    return events


def _merge_result(**overrides):
    base = dict(
        target_session_id="s1",
        target_assistant_id="asst_m",
        commit_id="commit_1",
        commit_parents=["p1"],
        final_text="merged",
        failed=False,
        error=None,
        base_peer=None,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


# --- 1. /merge slash path ---------------------------------------------------

def test_run_merge_syncs_mirror_head_on_success(monkeypatch, server_events):
    from openprogram.webui import server as _s
    from openprogram.webui._execute import _run_merge
    import openprogram.agent.internals._merge as merge_mod

    responses: list[dict] = []
    monkeypatch.setattr(
        _s, "_broadcast_chat_response",
        lambda sid, mid, resp: responses.append(resp),
    )
    monkeypatch.setattr(
        merge_mod, "process_merge_turn", lambda **kw: _merge_result(),
    )

    _run_merge(session_id="s1", msg_id="m1",
               kwargs={"sub_sessions": ["peer_x"], "message": "go"},
               agent_id="main")

    assert ("head", "s1", "asst_m") in server_events, \
        "merge reply head never reached the webui mirror — the next " \
        "_save_session flushes the pre-merge head back (orphaned reply)"
    assert responses and responses[-1]["type"] == "result"


def test_run_merge_does_not_touch_head_on_failure(monkeypatch, server_events):
    from openprogram.webui import server as _s
    from openprogram.webui._execute import _run_merge
    import openprogram.agent.internals._merge as merge_mod

    monkeypatch.setattr(_s, "_broadcast_chat_response",
                        lambda sid, mid, resp: None)
    monkeypatch.setattr(
        merge_mod, "process_merge_turn",
        lambda **kw: _merge_result(failed=True, error="boom",
                                   target_assistant_id=None),
    )

    _run_merge(session_id="s1", msg_id="m1",
               kwargs={"sub_sessions": ["peer_x"], "message": "go"},
               agent_id="main")

    assert not [e for e in server_events if e[0] == "head"]


# --- 2. WS merge_branches ---------------------------------------------------

def test_merge_branches_syncs_mirror_before_session_reload(
    monkeypatch, server_events,
):
    from openprogram.webui import server as _s
    import openprogram.webui.ws_actions.merge as ws_merge

    monkeypatch.setattr(_s, "_is_run_active", lambda sid: False)
    monkeypatch.setattr(
        ws_merge, "_run",
        lambda *a, **kw: {
            "target_assistant_id": "asst1", "commit_id": "c1",
            "commit_parents": [], "final_text": "x",
            "failed": False, "error": None, "base_peer": None,
        },
    )

    ws = _FakeWS()
    asyncio.run(ws_merge.handle_merge_branches(ws, {
        "session_id": "t1",
        "peers": [{"session_id": "peer_a"}],
        "message": "m",
        "agent_id": "main",
    }))

    result = ws.sent[0]
    assert result["type"] == "merge_branches_result"
    assert not result["data"]["failed"]
    head_moves = [e for e in server_events if e[0] == "head"]
    assert head_moves == [("head", "t1", "asst1")]
    # Mirror sync must precede the session_reload that makes clients
    # re-pull — otherwise they reload the stale branch.
    reload_idx = server_events.index(("broadcast", "session_reload"))
    assert server_events.index(head_moves[0]) < reload_idx


# --- 3. rewind partial failure ---------------------------------------------

def _rewind_payload(**overrides):
    base = dict(
        session_id="s1", target_msg_id="u2", user_text="redo this",
        turns_reverted=1, nodes_rewound=2, total_restored_paths=[],
        new_head_id="n1", errors=[],
    )
    base.update(overrides)
    return base


def test_rewind_partial_failure_still_syncs_mirror(monkeypatch, server_events):
    from openprogram.webui import server as _s
    import openprogram.agent._rewind as rewind_mod
    from openprogram.webui.ws_actions.chat import handle_rewind

    monkeypatch.setattr(_s, "_is_run_active", lambda sid: False)
    monkeypatch.setattr(
        rewind_mod, "rewind_to",
        lambda sid, target: _rewind_payload(errors=["restore failed: f.txt"]),
    )

    ws = _FakeWS()
    asyncio.run(handle_rewind(ws, {"session_id": "s1",
                                   "target_msg_id": "u2"}))

    # Head moved in the store, so the mirror must follow even though
    # file restore partially failed.
    assert ("head", "s1", "n1") in server_events
    frame = ws.sent[0]
    assert frame["type"] == "rewind_result"
    assert frame["data"]["errors"] == ["restore failed: f.txt"]


def test_rewind_full_failure_leaves_mirror_alone(monkeypatch, server_events):
    from openprogram.webui import server as _s
    import openprogram.agent._rewind as rewind_mod
    from openprogram.webui.ws_actions.chat import handle_rewind

    monkeypatch.setattr(_s, "_is_run_active", lambda sid: False)
    # The _err path: nothing was rewound, head never moved.
    monkeypatch.setattr(
        rewind_mod, "rewind_to",
        lambda sid, target: _rewind_payload(
            new_head_id=None, errors=["node 'u2' not found"],
            turns_reverted=0, nodes_rewound=0, user_text="",
        ),
    )

    ws = _FakeWS()
    asyncio.run(handle_rewind(ws, {"session_id": "s1",
                                   "target_msg_id": "u2"}))

    assert not [e for e in server_events if e[0] == "head"]


# --- 4. chat run-active guard ----------------------------------------------

def test_handle_chat_rejects_while_run_active(monkeypatch, server_events):
    from openprogram.webui import server as _s
    from openprogram.webui.ws_actions.chat import handle_chat

    monkeypatch.setattr(_s, "_get_or_create_session",
                        lambda sid, **kw: {"id": sid})
    monkeypatch.setattr(_s, "_is_run_active", lambda sid: True)
    appended: list = []
    monkeypatch.setattr(_s, "_append_msg",
                        lambda conv, msg: appended.append(msg))

    ws = _FakeWS()
    asyncio.run(handle_chat(ws, {"text": "hi", "session_id": "s1"}))

    assert appended == [], "racing turn wrote into the DAG despite the guard"
    assert len(ws.sent) == 1
    frame = ws.sent[0]
    assert frame["type"] == "chat_response"
    assert frame["data"]["type"] == "error"
    assert frame["data"]["code"] == "run_active"
    assert frame["data"]["content"] == _s.RUN_ACTIVE_ERROR
    assert not any(f.get("type") == "chat_ack" for f in ws.sent)
