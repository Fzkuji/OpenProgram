"""Agent archiving — archive_when_done + archive_agent.

Archiving removes a branch's right to be disturbed, never its history.
All fake — no real LLM, no live task pool run (async deliveries are
captured at run_agent_turn_async, the sync spawn at run_agent_turn;
same technique as test_send_message.py / test_agent_dispatch.py):

  * agent(archive_when_done=True): sync spawn archives at terminal;
    async spawn passes the flag + creator through to the runner, whose
    terminal hook stamps + archives; the flag survives Task round-trip
  * archive_agent: creator archives by address or name; non-creator
    refused; own top-level branch allowed; no session context allowed;
    already-archived is an idempotent notice
  * list_agents: scope="session" / "all" hide archived branches;
    scope="archived" lists exactly them
  * archived target: send_message and agent(to=) refuse via the one
    shared guard; read_conversation and agent(start_from="SID:MSG_ID")
    forks are unaffected

See docs/reference/design/runtime/agent-collaboration.md.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.functions.tools.agent.agent.agent import _agent_impl
from openprogram.functions.tools.send_message.archive_agent.archive_agent import (
    _archive_agent_impl,
)
from openprogram.functions.tools.send_message.list_agents.list_agents import (
    _list_agents_impl,
)
from openprogram.functions.tools.send_message.send_message.send_message import (
    _send_message_impl,
)


@pytest.fixture
def parent_turn(tmp_path, monkeypatch):
    """Isolated store, two sessions, parent turn bound on p1:a1.
    Async deliveries are captured at run_agent_turn_async; captured
    kwargs are exposed as ``store.async_calls``."""
    from openprogram.store.session.session_store import SessionStore
    from openprogram.agent import session_db as sdb_mod

    s = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr(sdb_mod, "default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.session_store.default_store", lambda: s)
    monkeypatch.setattr("openprogram.store.default_store", lambda: s)
    s.create_session("p1", "main", title="parent")
    s.append_message("p1", {"id": "u1", "role": "user", "content": "hi",
                            "timestamp": 0, "predecessor": None})
    s.append_message("p1", {"id": "a1", "role": "assistant", "content": "ok",
                            "timestamp": 0, "predecessor": "u1"})
    # A second, earlier branch tip (not the current turn) as a target.
    s.append_message("p1", {"id": "u0", "role": "user", "content": "older",
                            "timestamp": 0, "predecessor": "ROOT"})
    s.append_message("p1", {"id": "a0", "role": "assistant", "content": "older reply",
                            "timestamp": 0, "predecessor": "u0"})
    s.commit_turn("p1", "init")
    # A second session as a cross-session target.
    s.create_session("p2", "main", title="other")
    s.append_message("p2", {"id": "u9", "role": "user", "content": "x",
                            "timestamp": 0, "predecessor": None})
    s.append_message("p2", {"id": "a9", "role": "assistant", "content": "y",
                            "timestamp": 0, "predecessor": "u9"})
    s.commit_turn("p2", "init")

    from openprogram.agent import run_control
    from openprogram import store as store_mod
    sid_tok = run_control._current_session_id.set("p1")
    turn_tok = store_mod._current_turn_id.set("a1")

    calls: list[dict] = []

    def fake_async(**kw):
        calls.append(kw)
        return "t_fake"

    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn_async", fake_async)
    monkeypatch.setattr(
        "openprogram.agent.task.runner._broadcast", lambda *a, **k: None)
    s.async_calls = calls

    yield s
    run_control._current_session_id.reset(sid_tok)
    store_mod._current_turn_id.reset(turn_tok)
    import openprogram.agent.task.runner as runner_mod
    runner_mod.shutdown_runner()


# --- archive_when_done: terminal-state auto-archive ---

def test_sync_spawn_archive_when_done_archives_at_terminal(parent_turn, monkeypatch):
    """A blocking spawn with archive_when_done=True stamps the creator
    and archives the new branch after the result is in hand — and the
    result still flows back."""
    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn",
        lambda **kw: SimpleNamespace(
            head_id="sp1", final_text="done", failed=False, error=None),
    )
    out = _agent_impl("do it", archive_when_done=True)
    assert "done" in out
    meta = parent_turn.get_branch_meta("p1", "sp1")
    assert meta.get("archived") is True
    assert meta.get("archived_at")
    assert meta.get("spawner_session_id") == "p1"


def test_sync_spawn_default_keeps_agent_live(parent_turn, monkeypatch):
    """Default archive_when_done=False: the creator is stamped (so a
    later archive_agent can gate on it) but the branch stays live."""
    monkeypatch.setattr(
        "openprogram.agent.sub_agent_run.run_agent_turn",
        lambda **kw: SimpleNamespace(
            head_id="sp2", final_text="done", failed=False, error=None),
    )
    _agent_impl("do it")
    meta = parent_turn.get_branch_meta("p1", "sp2")
    assert not meta.get("archived")
    assert meta.get("spawner_session_id") == "p1"


def test_async_spawn_passes_flag_and_creator_to_runner(parent_turn):
    out = _agent_impl("do it", run_in_background=True, archive_when_done=True)
    assert "[agent spawned async]" in out
    kw = parent_turn.async_calls[-1]
    assert kw["spawner_session_id"] == "p1"
    assert kw["archive_when_done"] is True


def test_runner_terminal_hook_archives_spawn_branch(parent_turn):
    """The runner's terminal hook: spawner stamped always, archived only
    when the spawn asked for it; deliveries (no spawner) untouched."""
    from openprogram.agent.task import get_runner
    from openprogram.agent.task.types import Task

    runner = get_runner()
    runner._finalize_spawn_branch_meta(Task(
        id="t_x1", parent_session_id="p1", prompt="x", agent_id="main",
        head_id="a0", spawner_session_id="p1", archive_when_done=True,
    ))
    meta = parent_turn.get_branch_meta("p1", "a0")
    assert meta.get("archived") is True
    assert meta.get("spawner_session_id") == "p1"

    # A delivery task (spawner_session_id=None) never touches meta.
    runner._finalize_spawn_branch_meta(Task(
        id="t_x2", parent_session_id="p2", prompt="x", agent_id="main",
        head_id="a9", archive_when_done=True,
    ))
    assert parent_turn.get_branch_meta("p2", "a9") == {}


def test_task_roundtrip_preserves_archive_fields(parent_turn):
    from openprogram.agent.task.types import Task
    t = Task(id="t_r", parent_session_id="p1", prompt="x", agent_id="main",
             spawner_session_id="p1", archive_when_done=True)
    t2 = Task.from_dict(t.to_dict())
    assert t2.spawner_session_id == "p1"
    assert t2.archive_when_done is True


# --- archive_agent: by-name archiving + creator gate ---

def test_archive_agent_creator_archives_spawned_branch(parent_turn):
    """The session that spawned a branch (spawner_session_id) may
    archive it, even in another session."""
    parent_turn.set_branch_meta("p2", "a9", spawner_session_id="p1")
    out = _archive_agent_impl("p2:a9")
    assert "[archive_agent] archived p2:a9" in out
    assert parent_turn.get_branch_meta("p2", "a9").get("archived") is True


def test_archive_agent_non_creator_refused(parent_turn):
    parent_turn.set_branch_meta("p2", "a9", spawner_session_id="p3")
    out = _archive_agent_impl("p2:a9")
    assert "[archive_agent refused]" in out
    assert "created by session p3" in out
    assert not parent_turn.get_branch_meta("p2", "a9").get("archived")


def test_archive_agent_own_toplevel_branch_allowed(parent_turn):
    """A top-level branch of the current session (no spawner recorded)
    may be archived by the session itself."""
    out = _archive_agent_impl("p1:a0", reason="done with it")
    assert "[archive_agent] archived p1:a0" in out
    meta = parent_turn.get_branch_meta("p1", "a0")
    assert meta.get("archived") is True
    assert meta.get("archived_reason") == "done with it"


def test_archive_agent_foreign_toplevel_refused(parent_turn):
    out = _archive_agent_impl("p2:a9")
    assert "[archive_agent refused]" in out
    assert "another session" in out


def test_archive_agent_without_session_context_allowed(parent_turn, monkeypatch):
    """No session context (user / UI direct call) is not gated."""
    from openprogram.agent import run_control
    tok = run_control._current_session_id.set(None)
    try:
        out = _archive_agent_impl("p2:a9")
    finally:
        run_control._current_session_id.reset(tok)
    assert "[archive_agent] archived p2:a9" in out


def test_archive_agent_by_branch_name(parent_turn):
    parent_turn.set_branch_name("p1", "a0", "research")
    out = _archive_agent_impl("research")
    assert "[archive_agent] archived p1:a0" in out
    assert "«research»" in out


def test_archive_agent_already_archived_is_notice(parent_turn):
    _archive_agent_impl("p1:a0")
    out = _archive_agent_impl("p1:a0")
    assert "already archived" in out
    assert "refused" not in out and "error" not in out


def test_archive_agent_unknown_target_errors(parent_turn):
    out = _archive_agent_impl("p1:nosuchnode")
    assert "[archive_agent error]" in out and "not found" in out


# --- list_agents: scope visibility ---

def test_list_agents_scopes_hide_and_surface_archived(parent_turn):
    _archive_agent_impl("p1:a0")

    session_out = _list_agents_impl(scope="session")
    assert "p1:a1" in session_out
    assert "p1:a0" not in session_out

    all_out = _list_agents_impl(scope="all")
    assert "p1:a1" in all_out
    assert "p1:a0" not in all_out

    archived_out = _list_agents_impl(scope="archived")
    assert "p1:a0" in archived_out
    assert "p1:a1" not in archived_out
    assert "archived branch(es)" in archived_out


# --- archived target: deliveries refused, history untouched ---

def test_send_message_to_archived_branch_errors(parent_turn):
    _archive_agent_impl("p1:a0")
    out = _send_message_impl("hello", to="p1:a0")
    assert "[send_message error] agent p1:a0 is archived" in out
    # Nothing was submitted to the runner.
    assert parent_turn.async_calls == []


def test_agent_to_dispatch_to_archived_branch_errors(parent_turn):
    _archive_agent_impl("p1:a0")
    out = _agent_impl("do this", to="p1:a0")
    assert "[agent error] agent p1:a0 is archived" in out
    assert parent_turn.async_calls == []


def test_agent_to_with_archive_when_done_errors(parent_turn):
    out = _agent_impl("do this", to="p1:a0", archive_when_done=True)
    assert "[agent error]" in out
    assert "archive_when_done" in out
    assert parent_turn.async_calls == []


def test_read_conversation_still_reads_archived_branch(parent_turn):
    """read_conversation renders an archived branch — it never goes
    through the addressing guard, so the archive cannot hide history."""
    _archive_agent_impl("p1:a0")
    from openprogram.store.session.transcript import render_session_transcript

    out = render_session_transcript("p1", head_id="a0", store=parent_turn)
    assert "older reply" in out


def test_fork_from_archived_node_still_works(parent_turn):
    """agent(start_from="SID:MSG_ID") forks an archived branch's history —
    the archive removed the right to be disturbed, not the history."""
    _archive_agent_impl("p1:a0")
    out = _agent_impl("continue from here", start_from="p1:a0",
                      run_in_background=True)
    assert "[agent spawned async]" in out
    kw = parent_turn.async_calls[-1]
    assert kw["session_id"] == "p1"
    assert kw["branch_from"] == "a0"
