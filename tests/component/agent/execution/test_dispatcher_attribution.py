"""Public dispatcher entries restore attribution on every exit path."""
from contextlib import contextmanager
from contextvars import Context
from types import SimpleNamespace

import pytest

from openprogram.agent import dispatcher, plan_mode
from openprogram.agent.dispatcher.types import TurnRequest
from openprogram.usage.context import current_usage_context, usage_scope


def request(session_id):
    return TurnRequest(session_id=session_id, agent_id="main", user_text="test", source="agent_spawn")


def test_rejected_successive_turns_restore_attribution(monkeypatch):
    @contextmanager
    def denied(*_args):
        yield False

    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: object())
    monkeypatch.setattr("openprogram.self_update.control.maintenance.turn_admission", denied)

    def scenario():
        before = current_usage_context()
        for session_id in ("first", "second"):
            result = dispatcher.process_user_turn(request(session_id))
            assert result.failed
            assert current_usage_context() is before
            assert plan_mode.current_session_id.get() is None
    Context().run(scenario)


@pytest.mark.parametrize("error", [RuntimeError("database unavailable"), KeyboardInterrupt()])
def test_setup_failure_restores_enclosing_scope(monkeypatch, error):
    def fail():
        raise error
    monkeypatch.setattr("openprogram.agent.session_db.default_db", fail)

    def scenario():
        plan_mode.current_session_id.set("outer")
        with usage_scope(call_kind="exec", call_label="outer-label") as outer:
            with pytest.raises(type(error)) as caught:
                dispatcher.process_user_turn(request("inner"))
            assert caught.value is error
            assert current_usage_context() is outer
            assert plan_mode.current_session_id.get() == "outer"
    Context().run(scenario)


@pytest.mark.parametrize("safe_point", [False, True])
@pytest.mark.parametrize("outer_fields", [{}, {
    "call_kind": "exec", "call_label": "caller-label",
    "session_id": "caller-session", "agent_id": "caller-agent",
    "parent_session_id": "parent-session",
}])
def test_continuation_attribution_covers_loop_and_finalization(monkeypatch, safe_point, outer_fields):
    db = SimpleNamespace(
        get_session=lambda _: {}, message_exists=lambda *_: True,
        update_session=lambda *_a, **_kw: None,
    )
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.context.persistence.rendered_history", lambda *_a, **_kw: [])
    monkeypatch.setattr("openprogram.store.SessionNodeWriter", lambda *_: object())
    monkeypatch.setattr("openprogram.providers.registry.create_runtime", lambda: None)
    monkeypatch.setattr("openprogram.worktree.manager.get_manager", lambda: None)
    monkeypatch.setattr("openprogram.agent.session_config.load_session_run_config", lambda _: {})
    monkeypatch.setattr("openprogram.agent.dispatcher.persistence.persist_assistant_message",
                        lambda **_: ({"content": "reply"}, [], [], {}))
    observations = []

    def observe(phase):
        observations.append((phase, plan_mode.current_session_id.get(), current_usage_context()))

    def loop(**kwargs):
        observe("loop")
        kwargs["execution_context"]["safe_point_committed"] = safe_point
        return "reply", {}, []

    def finalize(**_kwargs):
        observe("finalize")
        return True

    monkeypatch.setattr(dispatcher, "_run_loop_blocking", loop)
    monkeypatch.setattr("openprogram.agent.dispatcher.finalize.finalize_turn", finalize)

    def scenario():
        plan_mode.current_session_id.set("caller-plan")
        with usage_scope(**outer_fields) as outer:
            dispatcher.process_agent_continuation(SimpleNamespace(
                request=request("resumed"), assistant_message_id="reply",
                state=SimpleNamespace(payload={"turn": {"user_message_id": "user"}}),
            ))
            assert [entry[0] for entry in observations] == (["loop"] if safe_point else ["loop", "finalize"])
            for _, plan_session, usage in observations:
                assert plan_session == "resumed"
                assert usage.session_id == (outer.session_id or "resumed")
                assert usage.agent_id == (outer.agent_id or "main")
                assert usage.call_kind == ("chat" if outer.call_kind == "unknown" else outer.call_kind)
                assert usage.call_label == outer.call_label
                assert usage.parent_session_id == outer.parent_session_id
            assert current_usage_context() is outer
            assert plan_mode.current_session_id.get() == "caller-plan"
    Context().run(scenario)
