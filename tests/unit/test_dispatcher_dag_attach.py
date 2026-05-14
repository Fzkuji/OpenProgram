"""dispatcher.process_user_turn attaches the active session's
GraphStore to a Runtime + installs it as the current_runtime ContextVar.

Verifies that an ``@agentic_function`` invoked during the turn records
its placeholder + exit-update + internal LLM Call(s) into the same
session DAG as the chat messages.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from openprogram.agent import dispatcher as D
from openprogram.agent.session_db import SessionDB
from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.context.storage import GraphStore


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions.sqlite")
    monkeypatch.setattr("openprogram.agent.session_db.default_db",
                        lambda: db)
    return db


def _stub_loop(text: str):
    """Replace _run_loop_blocking. The injected lambda runs INSIDE
    the dispatcher's try block, where the runtime/store is attached —
    we exercise an @agentic_function call from here and verify the
    nodes land in the session DAG."""
    def _stub(*, req, history, on_event, cancel_event):
        # Invoke an @agentic_function from inside the loop: it should
        # pick up the dispatcher-attached Runtime via _current_runtime
        # and write placeholder + LLM Call + exit-update to the DAG.
        @agentic_function
        def planner(task: str, runtime: Runtime = None):
            return runtime.exec(f"plan: {task}")

        # Need a runtime, but _current_runtime is set by dispatcher.
        from openprogram.agentic_programming.function import (
            _current_runtime,
        )
        rt = _current_runtime.get(None)
        assert rt is not None, "dispatcher should have set _current_runtime"
        # Override _call so the LLM "call" returns deterministic text
        rt._call = lambda content, model="default", response_format=None: "outline"
        rt._uses_legacy_call = lambda: True  # type: ignore[method-assign]

        result = planner("ship the feature", runtime=rt)
        assert result == "outline"

        return text, {"input_tokens": 1, "output_tokens": 1}, []
    return _stub


def test_dispatcher_attaches_store_so_agentic_function_lands_in_dag(tmp_db):
    """A turn that invokes an @agentic_function inside the agent_loop
    must record the function's entry placeholder + exit update + its
    internal runtime.exec node into the same session DAG as the
    user/assistant messages dispatcher writes."""
    with patch.object(D, "_run_loop_blocking",
                       side_effect=_stub_loop("final reply")):
        D.process_user_turn(
            D.TurnRequest(
                session_id="s1", agent_id="main",
                user_text="hello", source="cli",
            ),
            on_event=lambda _e: None,
        )

    store = GraphStore(tmp_db.db_path, "s1")
    g = store.load()

    # user message + agent code Call (planner) + agent's internal LLM
    # Call + final assistant message = at least 4 nodes. Order: chat
    # writes via DagSessionDB while @agentic_function writes directly
    # through Runtime; both land in the same nodes table.
    roles = [n.role for n in g]
    assert "user" in roles            # chat user msg
    assert "code" in roles            # planner code Call
    assert "llm" in roles             # runtime.exec inside planner

    # planner exit update filled in result
    planner_nodes = [n for n in g if n.is_code() and n.name == "planner"]
    assert len(planner_nodes) == 1
    assert planner_nodes[0].output == "outline"
    assert planner_nodes[0].metadata.get("status") == "success"

    # planner's internal LLM call has called_by = planner.id
    llm_nodes = [n for n in g if n.is_llm() and n.output == "outline"]
    assert len(llm_nodes) == 1
    assert llm_nodes[0].called_by == planner_nodes[0].id


def test_dispatcher_detaches_runtime_on_exception(tmp_db):
    """Even when _run_loop_blocking raises, dispatcher must detach the
    runtime / reset the ContextVar — otherwise the next turn would
    inherit a stale runtime."""
    from openprogram.agentic_programming.function import _current_runtime

    def _explode(*, req, history, on_event, cancel_event):
        raise RuntimeError("boom")

    pre = _current_runtime.get(None)
    with patch.object(D, "_run_loop_blocking", side_effect=_explode):
        D.process_user_turn(
            D.TurnRequest(
                session_id="s2", agent_id="main",
                user_text="hi", source="cli",
            ),
            on_event=lambda _e: None,
        )

    # ContextVar restored.
    assert _current_runtime.get(None) is pre
