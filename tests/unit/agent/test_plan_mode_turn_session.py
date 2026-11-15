"""A plan-mode tool finds its conversation after an approval resumed the turn."""
import contextvars
from types import SimpleNamespace

from openprogram.runtime.agent import turn_request
from openprogram.runtime.conversation import plan_mode


def test_the_bound_turn_request_stands_in_for_the_turn_start_variable(monkeypatch):
    def body():
        assert plan_mode.current_session_id.get() is None
        monkeypatch.setattr(turn_request, "get_turn_request",
                            lambda: SimpleNamespace(session_id="conversation-1"))
        return plan_mode.turn_session_id()

    assert contextvars.Context().run(body) == "conversation-1"


def test_the_turn_start_variable_wins_when_it_is_bound():
    def body():
        plan_mode.current_session_id.set("conversation-2")
        return plan_mode.turn_session_id()

    assert contextvars.Context().run(body) == "conversation-2"
