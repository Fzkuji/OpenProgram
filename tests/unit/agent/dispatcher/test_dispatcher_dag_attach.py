"""dispatcher.process_user_turn attaches the active session's
GraphStore to a Runtime + installs it as the current_runtime ContextVar.

Verifies that an ``Agent method`` invoked during the turn records
its placeholder + exit-update + internal LLM Call(s) into the same
session DAG as the chat messages.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from openprogram.runtime.conversation import turn_runtime as D
from openprogram.runtime.agent import turn_request as agent_turn_request
from openprogram.runtime.context.session_db import SessionDB
from openprogram.runtime.agent import Agent
from openprogram.runtime.agent.runtime import Runtime
from openprogram.state.store import SessionNodeWriter as GraphStore


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db",
                        lambda: db)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store",
                        lambda: db)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: db)

    # Select an explicit fake model/runtime. A model-setup failure must not
    # inherit the outer Runtime; this fixture tests DAG persistence rather
    # than depending on the developer's provider configuration.
    from types import SimpleNamespace
    monkeypatch.setattr(D, "_resolve_model", lambda *_args: SimpleNamespace(
        provider="dag-test", id="fake-model",
    ))
    monkeypatch.setattr("openprogram.runtime.agent.runtime.factory.create_runtime", lambda **_kwargs: Runtime(
        call=lambda content, model="default", response_format=None: "outline",
    ))
    return db


def _stub_loop(text: str):
    """Replace _run_loop_blocking. The injected lambda runs INSIDE
    the dispatcher's try block, where the runtime/store is attached —
    we exercise an Agent method call from here and verify the
    nodes land in the session DAG."""
    def _stub(*, req, history, on_event, cancel_event, **_extra):
        # Invoke an Agent method from inside the loop: it should
        # pick up the dispatcher-attached Runtime via _current_runtime
        # and write placeholder + LLM Call + exit-update to the DAG.
        class _PlannerAgent(Agent):
            method_options = {'planner': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'planner'}}

            def planner(self, task: str, runtime: Runtime = None):
                return runtime.exec(f"plan: {task}")

        planner = _PlannerAgent().planner

        # Grab the selected fake runtime installed by the dispatcher.
        from openprogram.runtime.agent.call_state import (
            _current_runtime,
        )
        rt = _current_runtime.get(None)
        assert rt is not None, (
            "_current_runtime must be installed by the dispatcher"
        )
        # Override _call so the LLM "call" returns deterministic text.
        # (Single exec path now — overriding _call is enough; the old
        # _uses_legacy_call override was removed.)
        rt._call = lambda content, model="default", response_format=None: "outline"

        result = planner("ship the feature", runtime=rt)
        assert result == "outline"

        return text, {"input_tokens": 1, "output_tokens": 1}, []
    return _stub


def test_dispatcher_attaches_store_so_agent_method_lands_in_dag(tmp_db):
    """A turn that invokes an Agent method inside the agent_loop
    must record the function's entry placeholder + exit update + its
    internal runtime.exec node into the same session DAG as the
    user/assistant messages dispatcher writes."""
    with patch.object(D, "_run_loop_blocking",
                       side_effect=_stub_loop("final reply")):
        D.process_user_turn(
            agent_turn_request.TurnRequest(
                session_id="s1", agent_id="main",
                user_text="hello", source="cli",
            ),
            on_event=lambda _e: None,
        )

    store = GraphStore(tmp_db, "s1")
    g = store.load()

    # user message + agent code Call (planner) + agent's internal LLM
    # Call + final assistant message = at least 4 nodes. Order: chat
    # writes via DagSessionDB while Agent method writes directly
    # through Runtime; both land in the same nodes table.
    roles = [n.role for n in g]
    assert "user" in roles            # chat user msg
    assert "code" in roles            # planner code Call
    assert "llm" in roles             # runtime.exec inside planner

    # The direct nested Agent owns a child graph; only its result is in chat.
    assert not any(n.name == 'planner' for n in g)
    link = next(n for n in g if n.metadata.get('child_session_id'))
    assert link.output == 'outline'
    child = GraphStore(tmp_db, link.metadata['child_session_id']).load()
    planner = next(n for n in child if n.name == 'planner')
    assert planner.output == 'outline'
    assert planner.metadata['status'] == 'completed'
    llm_nodes = [n for n in child if n.is_llm() and n.output == 'outline']
    assert len(llm_nodes) == 1
    assert llm_nodes[0].caller == planner.id


def test_dispatcher_detaches_runtime_on_exception(tmp_db):
    """Even when _run_loop_blocking raises, dispatcher must detach the
    runtime / reset the ContextVar — otherwise the next turn would
    inherit a stale runtime."""
    from openprogram.runtime.agent.call_state import _current_runtime

    def _explode(*, req, history, on_event, cancel_event, **_extra):
        raise RuntimeError("boom")

    pre = _current_runtime.get(None)
    with patch.object(D, "_run_loop_blocking", side_effect=_explode):
        D.process_user_turn(
            agent_turn_request.TurnRequest(
                session_id="s2", agent_id="main",
                user_text="hi", source="cli",
            ),
            on_event=lambda _e: None,
        )

    # ContextVar restored.
    assert _current_runtime.get(None) is pre
